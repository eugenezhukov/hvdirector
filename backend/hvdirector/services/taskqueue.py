"""Background task queue stored in PostgreSQL (executed by hvdirector-worker)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Task, User


def enqueue(
    db: Session,
    kind: str,
    *,
    user: User | None,
    tenant_id: uuid.UUID | None = None,
    target_type: str = "",
    target_id: uuid.UUID | None = None,
    target_name: str = "",
    params: dict[str, Any] | None = None,
    lock_key: str | None = None,
) -> Task:
    task = Task(
        kind=kind,
        user_id=user.id if user else None,
        username=user.username if user else "system",
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        params=params or {},
        lock_key=lock_key,
        status="queued",
    )
    db.add(task)
    db.flush()
    # Wake up the worker (it also polls, so a lost notification is harmless).
    db.execute(text("select pg_notify('hvd_tasks', :id)"), {"id": str(task.id)})
    return task


def task_to_dict(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "kind": t.kind,
        "status": t.status,
        "progress": t.progress,
        "message": t.message,
        "target_type": t.target_type,
        "target_id": str(t.target_id) if t.target_id else None,
        "target_name": t.target_name,
        "tenant_id": str(t.tenant_id) if t.tenant_id else None,
        "username": t.username,
        "result": t.result,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
    }
