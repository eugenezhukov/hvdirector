"""Tasks and audit events, visible according to the caller's role."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import Forbidden, NotFound
from ..models import Event, Task
from ..services.rbac import Principal
from ..services.taskqueue import task_to_dict
from .deps import current_principal
from .serializers import event_to_dict

router = APIRouter(prefix="/api", tags=["common"])


def _task_scope(q, p: Principal):  # noqa: ANN001
    if p.is_provider:
        return q
    q = q.where(Task.tenant_id == p.tenant_id)
    if p.has_tenant_perm("task.view"):
        return q
    vm_ids = p.scoped_vm_ids() if not p.sees_all_vms() else None
    conds = [Task.user_id == p.user.id]
    if p.sees_all_vms():
        conds.append(Task.target_type == "vm")
    elif vm_ids:
        conds.append(Task.target_id.in_(vm_ids))
    return q.where(or_(*conds))


@router.get("/tasks")
def list_tasks(limit: int = Query(default=30, le=500), target_id: uuid.UUID | None = None,
               status: str | None = None, tenant_id: uuid.UUID | None = None,
               p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    q = _task_scope(select(Task), p)
    if target_id:
        q = q.where(Task.target_id == target_id)
    if status:
        q = q.where(Task.status == status)
    if tenant_id and p.is_provider:
        q = q.where(Task.tenant_id == tenant_id)
    return [task_to_dict(t) for t in db.scalars(q.order_by(Task.created_at.desc()).limit(limit))]


@router.get("/tasks/{task_id}")
def get_task(task_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    t = db.scalar(_task_scope(select(Task).where(Task.id == task_id), p))
    if t is None:
        raise NotFound("task")
    return task_to_dict(t)


@router.get("/events")
def list_events(limit: int = Query(default=100, le=1000), offset: int = 0, action: str | None = None,
                q: str | None = None, tenant_id: uuid.UUID | None = None,
                p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    stmt = select(Event)
    if not p.is_provider:
        if not p.has_tenant_perm("event.view"):
            raise Forbidden("Permission 'event.view' required")
        stmt = stmt.where(Event.tenant_id == p.tenant_id)
    elif tenant_id:
        stmt = stmt.where(Event.tenant_id == tenant_id)
    if action:
        stmt = stmt.where(Event.action.like(f"{action}%"))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Event.username.ilike(like), Event.target_name.ilike(like), Event.action.ilike(like)))
    rows = db.scalars(stmt.order_by(Event.ts.desc()).offset(offset).limit(limit + 1)).all()
    return {"items": [event_to_dict(e) for e in rows[:limit]], "has_more": len(rows) > limit}
