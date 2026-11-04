"""Authentication / authorisation dependencies."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AppError, Forbidden, NotFound
from ..models import VM, RoleAssignment, Tenant, User, UserSession, utcnow
from ..security import hash_token, tokens_equal
from ..services.rbac import Principal
from ..services.settings import get_setting

SESSION_COOKIE = "hvd_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class Unauthorized(AppError):
    status_code = 401

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__("unauthorized", message)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def load_principal(db: Session, token: str | None, ip: str = "") -> Principal | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if sess is None:
        return None
    now = utcnow()
    if sess.expires_at <= now:
        db.delete(sess)
        db.commit()
        return None
    sec = get_setting(db, "security")
    idle = dt.timedelta(minutes=int(sec.get("session_idle_minutes", 480)))
    if sess.last_seen_at and now - sess.last_seen_at > idle:
        db.delete(sess)
        db.commit()
        return None
    user = sess.user
    if user is None or not user.is_active:
        return None
    tenant = None
    if user.tenant_id is not None:
        tenant = db.get(Tenant, user.tenant_id)
        if tenant is None or not tenant.enabled:
            return None
    if (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
        db.commit()
    assignments = [(r.role, r.vm_id) for r in db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == user.id))]
    return Principal(user=user, session=sess, tenant=tenant, assignments=assignments, ip=ip)


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    principal = load_principal(db, request.cookies.get(SESSION_COOKIE), client_ip(request))
    if principal is None:
        raise Unauthorized()
    if request.method not in SAFE_METHODS:
        token = request.headers.get(CSRF_HEADER, "")
        if not token or principal.session is None or not tokens_equal(token, principal.session.csrf_token):
            raise AppError("csrf_failed", "CSRF token missing or invalid", status_code=403)
    return principal


def provider_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.is_provider:
        raise Forbidden("Provider administrator required")
    return principal


def tenant_user(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.is_provider or principal.tenant is None:
        raise Forbidden("Tenant user required")
    return principal


def require_tenant_perm(principal: Principal, perm: str) -> None:
    if not principal.has_tenant_perm(perm):
        raise Forbidden(f"Permission '{perm}' required")


def get_vm_for(db: Session, principal: Principal, vm_id: uuid.UUID, perm: str = "vm.view") -> VM:
    vm = db.get(VM, vm_id)
    if vm is None or vm.tenant_id != principal.tenant_id or vm.kind != "vm":
        raise NotFound("vm")
    if not principal.has_vm_perm("vm.view", vm.id):
        raise NotFound("vm")
    if not principal.has_vm_perm(perm, vm.id):
        raise Forbidden(f"Permission '{perm}' required")
    return vm


def user_display(u: User) -> str:
    return u.full_name or u.username
