from __future__ import annotations

import datetime as dt
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_config
from ..db import get_db
from ..errors import AppError
from ..models import Tenant, User, UserSession, utcnow
from ..security import (hash_password, hash_token, new_token, password_needs_rehash, validate_password_strength,
                        verify_password)
from ..services.audit import log_event
from ..services.rbac import Principal
from ..services.settings import get_setting
from .deps import SESSION_COOKIE, client_ip, current_principal, load_principal

router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[str, deque[float]] = defaultdict(deque)
_attempts_lock = threading.Lock()


def _rate_limited(ip: str, limit: int = 20, window: float = 60.0) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        q = _attempts[ip]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


class LoginIn(BaseModel):
    org: str | None = Field(default=None, max_length=64)
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


class LocaleIn(BaseModel):
    locale: str = Field(pattern=r"^(ru|en)$")


def me_payload(p: Principal) -> dict:
    u = p.user
    roles = [{"role": r, "vm_id": str(v) if v else None} for r, v in p.assignments]
    return {
        "user": {"id": str(u.id), "username": u.username, "full_name": u.full_name, "email": u.email,
                 "locale": u.locale, "is_provider": p.is_provider},
        "tenant": ({"id": str(p.tenant.id), "slug": p.tenant.slug, "name": p.tenant.name} if p.tenant else None),
        "roles": roles,
        "permissions": ["provider.admin"] if p.is_provider else p.all_permissions(),
        "csrf_token": p.session.csrf_token if p.session else None,
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    if _rate_limited(ip):
        raise AppError("too_many_attempts", "Too many login attempts, try again later", status_code=429)
    sec = get_setting(db, "security")
    org = (body.org or "").strip().lower()
    tenant = None
    if org:
        tenant = db.scalar(select(Tenant).where(func.lower(Tenant.slug) == org))
        if tenant is None:
            log_event(db, "auth.login", username=body.username, status="failure", ip=ip,
                      details={"org": org, "reason": "unknown_org"})
            db.commit()
            raise AppError("invalid_credentials", "Invalid organization, user name or password", status_code=401)
        user = db.scalar(select(User).where(User.tenant_id == tenant.id,
                                            func.lower(User.username) == body.username.strip().lower()))
    else:
        user = db.scalar(select(User).where(User.tenant_id.is_(None),
                                            func.lower(User.username) == body.username.strip().lower()))
    now = utcnow()
    if user is not None and user.locked_until and user.locked_until > now:
        raise AppError("account_locked", "Account is temporarily locked", status_code=401,
                       until=user.locked_until.isoformat())
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= int(sec.get("max_failed_logins", 10)):
                user.locked_until = now + dt.timedelta(minutes=int(sec.get("lockout_minutes", 15)))
                user.failed_logins = 0
        log_event(db, "auth.login", user=user, username=body.username, tenant_id=tenant.id if tenant else None,
                  status="failure", ip=ip, details={"org": org or None})
        db.commit()
        raise AppError("invalid_credentials", "Invalid organization, user name or password", status_code=401)
    if tenant is not None and not tenant.enabled:
        raise AppError("tenant_disabled", "Organization is disabled", status_code=403)
    if tenant is not None and not user.roles:
        raise AppError("no_roles", "No roles assigned to this user", status_code=403)

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    token = new_token()
    max_hours = int(sec.get("session_max_hours", 24))
    sess = UserSession(user_id=user.id, token_hash=hash_token(token), csrf_token=new_token(24), ip=ip,
                       user_agent=(request.headers.get("user-agent") or "")[:255], last_seen_at=now,
                       expires_at=now + dt.timedelta(hours=max_hours))
    db.add(sess)
    log_event(db, "auth.login", user=user, ip=ip)
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=get_config().cookie_secure, samesite="lax",
                        path="/", max_age=max_hours * 3600)
    principal = load_principal(db, token, ip)
    assert principal is not None
    return me_payload(principal)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
        if sess is not None:
            log_event(db, "auth.logout", user=sess.user, ip=client_ip(request))
            db.delete(sess)
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return me_payload(p)


@router.post("/password")
def change_password(body: PasswordIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, p.user.id)
    if not verify_password(user.password_hash, body.current_password):
        raise AppError("invalid_password", "Current password is wrong")
    err = validate_password_strength(body.new_password)
    if err:
        raise AppError(err, "Password does not meet the policy")
    user.password_hash = hash_password(body.new_password)
    # Terminate other sessions of this user
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
        if p.session is None or s.id != p.session.id:
            db.delete(s)
    log_event(db, "user.password_changed", user=user, target_type="user", target_id=user.id,
              target_name=user.username, ip=p.ip)
    db.commit()
    return {"ok": True}


@router.put("/locale")
def set_locale(body: LocaleIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, p.user.id)
    user.locale = body.locale
    db.commit()
    return {"locale": body.locale}
