"""Provider API: management of tenant VMs (power, parameters, load graphs).

The provider acts on behalf of the organization: changes go through the same
validation and tenant quotas as tenant requests, tasks are attributed to the
provider administrator and are visible to the organization, every action is
written to the audit log.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AppError, Conflict, NotFound
from ..models import VM, Network, Task
from ..services import metrics as metrics_svc
from ..services.audit import log_event
from ..services.rbac import Principal
from ..services.taskqueue import enqueue, task_to_dict
from ..services.vmconfig import plan_reconfigure, tenant_storage_classes
from .deps import provider_admin
from .serializers import vm_to_dict
from .tenant import POWER_ACTIONS, ReconfigureIn

router = APIRouter(prefix="/api/provider", tags=["provider-vms"])


class PowerIn(BaseModel):
    action: str


def _vm(db: Session, vm_id: uuid.UUID) -> VM:
    vm = db.get(VM, vm_id)
    if vm is None:
        raise NotFound("vm")
    return vm


def _ready(vm: VM) -> None:
    if vm.status != "ready" or not vm.hv_id:
        raise Conflict("vm_not_ready", "VM is not in a ready state", status=vm.status)


def _task(db: Session, p: Principal, vm: VM, kind: str, params: dict | None = None) -> Task:
    return enqueue(db, kind, user=p.user, tenant_id=vm.tenant_id, target_type="vm", target_id=vm.id,
                   target_name=vm.name, params=params or {}, lock_key=f"vm:{vm.id}")


@router.get("/vms/{vm_id}")
def get_vm(vm_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    vm = _vm(db, vm_id)
    out = vm_to_dict(vm, detail=True)
    nets = db.scalars(select(Network).where(Network.tenant_id == vm.tenant_id).order_by(Network.name)).all()
    out["options"] = {
        "networks": [{"id": str(n.id), "name": n.name, "vlan_id": n.vlan_id} for n in nets],
        "storage_classes": tenant_storage_classes(db, vm.tenant),
    }
    return out


@router.post("/vms/{vm_id}/power")
def power(vm_id: uuid.UUID, body: PowerIn, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    vm = _vm(db, vm_id)
    if body.action not in POWER_ACTIONS:
        raise AppError("invalid_action", "Unknown power action", action=body.action)
    _ready(vm)
    task = _task(db, p, vm, "vm.power", {"action": body.action})
    log_event(db, f"vm.power.{body.action}", user=p.user, tenant_id=vm.tenant_id, target_type="vm", target_id=vm.id,
              target_name=vm.name, ip=p.ip, details={"by": "provider"})
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/refresh")
def refresh(vm_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    vm = _vm(db, vm_id)
    _ready(vm)
    task = _task(db, p, vm, "vm.refresh")
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/reconfigure")
def reconfigure(vm_id: uuid.UUID, body: ReconfigureIn, p: Principal = Depends(provider_admin),
                db: Session = Depends(get_db)) -> dict:
    vm = _vm(db, vm_id)
    params = plan_reconfigure(db, vm, body)
    task = _task(db, p, vm, "vm.reconfigure", params)
    log_event(db, "vm.reconfigure", user=p.user, tenant_id=vm.tenant_id, target_type="vm", target_id=vm.id,
              target_name=vm.name, ip=p.ip, details={"by": "provider", **body.model_dump(mode="json")})
    db.commit()
    return task_to_dict(task)


@router.get("/vms/{vm_id}/metrics")
def vm_metrics(vm_id: uuid.UUID, range: str = "1h", p: Principal = Depends(provider_admin),  # noqa: A002
               db: Session = Depends(get_db)) -> dict:
    return metrics_svc.series(db, _vm(db, vm_id), range)
