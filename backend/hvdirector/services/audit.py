"""Audit log (events)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..models import Event, User

log = logging.getLogger("hvdirector.audit")


def log_event(
    db: Session,
    action: str,
    *,
    user: User | None = None,
    username: str | None = None,
    tenant_id: uuid.UUID | None = None,
    target_type: str = "",
    target_id: Any = "",
    target_name: str = "",
    status: str = "success",
    details: dict[str, Any] | None = None,
    ip: str = "",
) -> Event:
    ev = Event(
        action=action,
        user_id=user.id if user else None,
        username=(user.username if user else username) or "system",
        tenant_id=tenant_id if tenant_id is not None else (user.tenant_id if user else None),
        target_type=target_type,
        target_id=str(target_id or ""),
        target_name=target_name or "",
        status=status,
        details=details or {},
        ip=ip or "",
    )
    db.add(ev)
    log.info("event %s by %s on %s %s: %s", action, ev.username, target_type, target_name, status)
    return ev
