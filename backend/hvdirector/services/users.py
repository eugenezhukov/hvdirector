"""Local user database helpers (shared by provider and tenant APIs)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import AppError, Conflict, NotFound
from ..models import VM, RoleAssignment, User, UserSession
from ..security import hash_password, validate_password_strength
from ..utils import USERNAME_RE
from .rbac import ROLES


def _check_username(db: Session, tenant_id: uuid.UUID | None, username: str, exclude: uuid.UUID | None = None) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise AppError("invalid_username", "User name may contain letters, digits and . _ - @")
    q = select(User).where(func.lower(User.username) == username.lower())
    q = q.where(User.tenant_id.is_(None)) if tenant_id is None else q.where(User.tenant_id == tenant_id)
    existing = db.scalar(q)
    if existing is not None and existing.id != exclude:
        raise Conflict("user_exists", f"User {username} already exists", username=username)
    return username


def _check_password(password: str) -> None:
    err = validate_password_strength(password)
    if err:
        raise AppError(err, "Password does not meet the policy (min 8 chars, 2 character classes)")


def create_user(db: Session, tenant_id: uuid.UUID | None, data: dict[str, Any]) -> User:
    username = _check_username(db, tenant_id, data["username"])
    _check_password(data["password"])
    user = User(tenant_id=tenant_id, username=username, full_name=data.get("full_name") or "",
                email=data.get("email") or "", password_hash=hash_password(data["password"]),
                is_active=data.get("is_active", True), locale=data.get("locale") or "ru")
    db.add(user)
    db.flush()
    return user


def update_user(db: Session, user: User, data: dict[str, Any]) -> User:
    if data.get("username") is not None and data["username"] != user.username:
        user.username = _check_username(db, user.tenant_id, data["username"], exclude=user.id)
    for field in ("full_name", "email", "locale"):
        if data.get(field) is not None:
            setattr(user, field, data[field])
    if data.get("is_active") is not None:
        user.is_active = bool(data["is_active"])
        if not user.is_active:
            db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    if data.get("password"):
        _check_password(data["password"])
        user.password_hash = hash_password(data["password"])
        user.locked_until = None
        user.failed_logins = 0
        db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    if data.get("unlock"):
        user.locked_until = None
        user.failed_logins = 0
    return user


def set_roles(db: Session, user: User, roles: list[dict[str, Any]]) -> None:
    """Replace role assignments: [{"role": "...", "vm_id": null|uuid}, ...]."""
    if user.tenant_id is None:
        raise AppError("provider_user_roles", "Provider administrators have no tenant roles")
    wanted: set[tuple[str, uuid.UUID | None]] = set()
    for r in roles:
        role = r.get("role")
        if role not in ROLES:
            raise AppError("invalid_role", f"Unknown role {role}", role=role)
        vm_id = uuid.UUID(str(r["vm_id"])) if r.get("vm_id") else None
        if vm_id is not None:
            vm = db.get(VM, vm_id)
            if vm is None or vm.tenant_id != user.tenant_id or vm.kind != "vm":
                raise NotFound("vm")
        wanted.add((role, vm_id))
    current = {(a.role, a.vm_id): a for a in user.roles}
    for key, a in current.items():
        if key not in wanted:
            db.delete(a)
    for role, vm_id in wanted:
        if (role, vm_id) not in current:
            db.add(RoleAssignment(user_id=user.id, tenant_id=user.tenant_id, role=role, vm_id=vm_id))
    db.flush()
    db.expire(user, ["roles"])


def count_tenant_admins(db: Session, tenant_id: uuid.UUID, exclude_user: uuid.UUID | None = None) -> int:
    q = (select(func.count(func.distinct(RoleAssignment.user_id)))
         .join(User, User.id == RoleAssignment.user_id)
         .where(RoleAssignment.tenant_id == tenant_id, RoleAssignment.role == "tenant_admin",
                RoleAssignment.vm_id.is_(None), User.is_active.is_(True)))
    if exclude_user is not None:
        q = q.where(RoleAssignment.user_id != exclude_user)
    return int(db.scalar(q) or 0)
