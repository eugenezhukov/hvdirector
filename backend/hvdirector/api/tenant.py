"""Tenant (organization) self-service API."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..errors import AppError, Conflict, Forbidden, NotFound
from ..models import (VM, ConsoleTicket, Datastore, Iso, Network, Router, RouterNetwork, Snapshot, StorageClass, Task,
                      TenantStorageQuota, TenantVlan, User, VMDisk, VMNic, utcnow)
from ..security import hash_token, new_token
from ..services import isos as iso_svc
from ..services import netutil
from ..services import routers as rsvc
from ..services import users as user_svc
from ..services.audit import log_event
from ..services.quotas import Delta, check_quota, lock_tenant, quota_report
from ..services.rbac import Principal
from ..services.settings import get_setting
from ..services.taskqueue import enqueue, task_to_dict
from ..utils import NETWORK_NAME_RE, VM_NAME_RE, hv_vm_name
from .deps import get_vm_for, require_tenant_perm, tenant_user
from .serializers import iso_to_dict, network_to_dict, storage_class_to_dict, user_to_dict, vm_to_dict

router = APIRouter(prefix="/api/tenant", tags=["tenant"])

POWER_ACTIONS = {"start", "shutdown", "poweroff", "reset", "reboot", "save"}


def _visible_vms_query(p: Principal):
    q = select(VM).where(VM.tenant_id == p.tenant_id, VM.kind == "vm")
    if not p.sees_all_vms():
        ids = p.scoped_vm_ids()
        q = q.where(VM.id.in_(ids or {uuid.uuid4()}))
    return q


# ==========================================================================
# Summary / quotas
# ==========================================================================


@router.get("/summary")
def summary(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    t = p.tenant
    vms = db.scalars(_visible_vms_query(p)).all()
    out = {
        "tenant": {"id": str(t.id), "slug": t.slug, "name": t.name, "description": t.description,
                   "cluster": t.cluster.name if t.cluster else None},
        "vms_total": len(vms),
        "vms_running": sum(1 for v in vms if v.power_state == "Running"),
        "vms_error": sum(1 for v in vms if v.status == "error"),
    }
    if p.has_tenant_perm("tenant.view"):
        out["quotas"] = quota_report(db, t)
        out["networks"] = db.scalar(select(func.count(Network.id)).where(Network.tenant_id == t.id)) or 0
        out["isos"] = db.scalar(select(func.count(Iso.id)).where(Iso.tenant_id == t.id)) or 0
        out["users"] = db.scalar(select(func.count(User.id)).where(User.tenant_id == t.id)) or 0
    return out


@router.get("/storage-classes")
def storage_classes(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    report = {s["storage_class_id"]: s for s in quota_report(db, p.tenant)["storage"]}
    out = []
    for q in db.scalars(select(TenantStorageQuota).where(TenantStorageQuota.tenant_id == p.tenant_id)):
        sc = db.get(StorageClass, q.storage_class_id)
        has_ds = db.scalar(select(func.count(Datastore.id)).where(
            Datastore.cluster_id == p.tenant.cluster_id, Datastore.storage_class_id == sc.id,
            Datastore.enabled.is_(True), Datastore.present.is_(True))) or 0
        out.append(storage_class_to_dict(sc) | {"limit_gb": q.limit_gb,
                                               "used_gb": report.get(str(sc.id), {}).get("used_gb", 0),
                                               "available": bool(has_ds)})
    out.sort(key=lambda x: (x["tier"], x["name"]))
    return out


# ==========================================================================
# Virtual machines
# ==========================================================================


class DiskIn(BaseModel):
    size_gb: int = Field(ge=1, le=65536)
    storage_class_id: uuid.UUID


class NicIn(BaseModel):
    network_id: uuid.UUID | None = None


class VmCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    description: str = ""
    generation: Literal[1, 2] = 2
    secure_boot: Literal["off", "windows", "linux"] = "windows"
    vcpu: int = Field(ge=1, le=240)
    memory_mb: int = Field(ge=256, le=12 * 1024 * 1024)
    disks: list[DiskIn] = Field(min_length=1, max_length=16)
    nics: list[NicIn] = Field(default=[], max_length=8)
    iso_id: uuid.UUID | None = None
    power_on: bool = False


class VmUpdateIn(BaseModel):
    description: str | None = None


class PowerIn(BaseModel):
    action: str


class SnapshotIn(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str = ""


class MediaIn(BaseModel):
    iso_id: uuid.UUID | None = None


class NicOpIn(BaseModel):
    op: Literal["add", "set", "remove"]
    nic_id: uuid.UUID | None = None
    network_id: uuid.UUID | None = None


class ReconfigureIn(BaseModel):
    vcpu: int | None = Field(default=None, ge=1, le=240)
    memory_mb: int | None = Field(default=None, ge=256, le=12 * 1024 * 1024)
    add_disks: list[DiskIn] = Field(default=[], max_length=8)
    nics: list[NicOpIn] = Field(default=[], max_length=8)


def _pick_datastore(db: Session, cluster_id: uuid.UUID, storage_class_id: uuid.UUID, ha: bool) -> Datastore:
    rows = db.scalars(select(Datastore).where(
        Datastore.cluster_id == cluster_id, Datastore.storage_class_id == storage_class_id,
        Datastore.enabled.is_(True), Datastore.present.is_(True))).all()
    if ha:
        rows = [d for d in rows if d.kind in ("csv", "smb")]
    if not rows:
        sc = db.get(StorageClass, storage_class_id)
        raise AppError("no_datastore_for_class", "No datastore of this storage class on the tenant cluster",
                       storage_class=sc.name if sc else str(storage_class_id))
    return max(rows, key=lambda d: d.free_gb or 0)


def _tenant_network(db: Session, p: Principal, network_id: uuid.UUID) -> Network:
    net = db.get(Network, network_id)
    if net is None or net.tenant_id != p.tenant_id:
        raise NotFound("network")
    return net


def _visible_iso(db: Session, p: Principal, iso_id: uuid.UUID) -> Iso:
    iso = db.get(Iso, iso_id)
    if iso is None or (iso.tenant_id is not None and iso.tenant_id != p.tenant_id):
        raise NotFound("iso")
    if iso.status != "ready":
        raise Conflict("iso_not_ready", "ISO upload is not finished")
    return iso


def _require_ready(vm: VM) -> None:
    if vm.status != "ready" or not vm.hv_id:
        raise Conflict("vm_not_ready", "VM is not in a ready state", status=vm.status)


def _vm_task(db: Session, p: Principal, vm: VM, kind: str, params: dict | None = None) -> Task:
    return enqueue(db, kind, user=p.user, tenant_id=vm.tenant_id, target_type="vm", target_id=vm.id,
                   target_name=vm.name, params=params or {}, lock_key=f"vm:{vm.id}")


@router.get("/vms")
def list_vms(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    return [vm_to_dict(v, p) for v in db.scalars(_visible_vms_query(p).order_by(VM.name))]


@router.post("/vms")
def create_vm(body: VmCreateIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "vm.create")
    name = body.name.strip()
    if not VM_NAME_RE.match(name):
        raise AppError("invalid_vm_name", "VM name: letters, digits, '-', '_', '.', up to 63 chars")
    tenant = lock_tenant(db, p.tenant_id)
    if db.scalar(select(VM).where(VM.tenant_id == tenant.id, func.lower(VM.name) == name.lower())):
        raise Conflict("vm_exists", "VM with this name already exists", name=name)
    cluster = tenant.cluster
    ha = cluster.kind == "cluster" and cluster.ha_enabled
    memory_mb = body.memory_mb + (body.memory_mb % 2)
    if body.generation == 1 and body.secure_boot != "off":
        body.secure_boot = "off"
    storage: dict[uuid.UUID, int] = {}
    for d in body.disks:
        storage[d.storage_class_id] = storage.get(d.storage_class_id, 0) + d.size_gb
    check_quota(db, tenant, Delta(vcpu=body.vcpu, memory_mb=memory_mb, vms=1, storage_gb=storage))
    nets = [_tenant_network(db, p, n.network_id) if n.network_id else None for n in body.nics]
    iso = _visible_iso(db, p, body.iso_id) if body.iso_id else None

    vm = VM(tenant_id=tenant.id, cluster_id=cluster.id, name=name, description=body.description,
            hv_name=hv_vm_name(tenant.slug, name), generation=body.generation, secure_boot=body.secure_boot,
            vcpu=body.vcpu, memory_mb=memory_mb, status="creating", power_state="Unknown",
            iso_id=iso.id if iso else None, created_by=p.user.id)
    db.add(vm)
    db.flush()
    for i, d in enumerate(body.disks):
        ds = _pick_datastore(db, cluster.id, d.storage_class_id, ha)
        db.add(VMDisk(vm_id=vm.id, datastore_id=ds.id, storage_class_id=d.storage_class_id, size_gb=d.size_gb,
                      position=i))
    for i, net in enumerate(nets):
        db.add(VMNic(vm_id=vm.id, network_id=net.id if net else None, position=i, hv_name=f"nic{i}"))
    db.flush()
    task = _vm_task(db, p, vm, "vm.create", {"power_on": body.power_on})
    log_event(db, "vm.create", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip,
              details={"vcpu": vm.vcpu, "memory_mb": vm.memory_mb, "disks": [d.size_gb for d in body.disks]})
    db.commit()
    db.refresh(vm)
    return {"vm": vm_to_dict(vm, p, detail=True), "task": task_to_dict(task)}


@router.get("/vms/{vm_id}")
def get_vm(vm_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id)
    return vm_to_dict(vm, p, detail=True)


@router.patch("/vms/{vm_id}")
def update_vm(vm_id: uuid.UUID, body: VmUpdateIn, p: Principal = Depends(tenant_user),
              db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.edit")
    if body.description is not None:
        vm.description = body.description
    db.commit()
    return vm_to_dict(vm, p, detail=True)


@router.delete("/vms/{vm_id}")
def delete_vm(vm_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.delete")
    if vm.status in ("creating", "deleting"):
        raise Conflict("vm_busy", "VM is being created or deleted")
    vm.status = "deleting"
    task = _vm_task(db, p, vm, "vm.delete")
    log_event(db, "vm.delete", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip)
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/power")
def power(vm_id: uuid.UUID, body: PowerIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.power")
    if body.action not in POWER_ACTIONS:
        raise AppError("invalid_action", "Unknown power action", action=body.action)
    _require_ready(vm)
    task = _vm_task(db, p, vm, "vm.power", {"action": body.action})
    log_event(db, f"vm.power.{body.action}", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name,
              ip=p.ip)
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/refresh")
def refresh_vm(vm_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id)
    _require_ready(vm)
    task = _vm_task(db, p, vm, "vm.refresh")
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/snapshots")
def create_snapshot(vm_id: uuid.UUID, body: SnapshotIn, p: Principal = Depends(tenant_user),
                    db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.snapshot")
    _require_ready(vm)
    tenant = lock_tenant(db, p.tenant_id)
    check_quota(db, tenant, Delta(snapshots=1))
    name = (body.name or "").strip() or f"{vm.name}-{utcnow().strftime('%Y%m%d-%H%M%S')}"
    snap = Snapshot(vm_id=vm.id, tenant_id=tenant.id, name=name, description=body.description, status="creating",
                    created_by=p.user.id)
    db.add(snap)
    db.flush()
    task = _vm_task(db, p, vm, "vm.snapshot.create", {"snapshot_id": str(snap.id)})
    log_event(db, "vm.snapshot.create", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip,
              details={"snapshot": name})
    db.commit()
    return task_to_dict(task)


def _get_snapshot(db: Session, vm: VM, snap_id: uuid.UUID) -> Snapshot:
    snap = db.get(Snapshot, snap_id)
    if snap is None or snap.vm_id != vm.id:
        raise NotFound("snapshot")
    return snap


@router.post("/vms/{vm_id}/snapshots/{snap_id}/revert")
def revert_snapshot(vm_id: uuid.UUID, snap_id: uuid.UUID, p: Principal = Depends(tenant_user),
                    db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.snapshot")
    _require_ready(vm)
    snap = _get_snapshot(db, vm, snap_id)
    if snap.status != "ready":
        raise Conflict("snapshot_not_ready", "Snapshot is not ready")
    task = _vm_task(db, p, vm, "vm.snapshot.revert", {"snapshot_id": str(snap.id)})
    log_event(db, "vm.snapshot.revert", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip,
              details={"snapshot": snap.name})
    db.commit()
    return task_to_dict(task)


@router.delete("/vms/{vm_id}/snapshots/{snap_id}")
def delete_snapshot(vm_id: uuid.UUID, snap_id: uuid.UUID, p: Principal = Depends(tenant_user),
                    db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.snapshot")
    snap = _get_snapshot(db, vm, snap_id)
    if snap.status == "creating":
        raise Conflict("snapshot_not_ready", "Snapshot is being created")
    snap.status = "deleting"
    task = _vm_task(db, p, vm, "vm.snapshot.delete", {"snapshot_id": str(snap.id)})
    log_event(db, "vm.snapshot.delete", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip,
              details={"snapshot": snap.name})
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/media")
def media(vm_id: uuid.UUID, body: MediaIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.media")
    _require_ready(vm)
    iso = _visible_iso(db, p, body.iso_id) if body.iso_id else None
    task = _vm_task(db, p, vm, "vm.media", {"iso_id": str(iso.id) if iso else None})
    log_event(db, "vm.media.insert" if iso else "vm.media.eject", user=p.user, target_type="vm", target_id=vm.id,
              target_name=vm.name, ip=p.ip, details={"iso": iso.name if iso else None})
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/reconfigure")
def reconfigure(vm_id: uuid.UUID, body: ReconfigureIn, p: Principal = Depends(tenant_user),
                db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.edit")
    _require_ready(vm)
    tenant = lock_tenant(db, p.tenant_id)
    cluster = tenant.cluster
    params: dict = {"new_disk_ids": [], "nic_ops": []}
    delta = Delta()
    cpu_mem_change = (body.vcpu and body.vcpu != vm.vcpu) or (body.memory_mb and body.memory_mb != vm.memory_mb)
    if cpu_mem_change:
        if vm.power_state != "Off":
            raise Conflict("vm_must_be_off", "Power off the VM to change vCPU or memory")
        if body.vcpu and body.vcpu != vm.vcpu:
            delta.vcpu = body.vcpu - vm.vcpu
        if body.memory_mb and body.memory_mb != vm.memory_mb:
            delta.memory_mb = body.memory_mb + (body.memory_mb % 2) - vm.memory_mb
    for d in body.add_disks:
        delta.storage_gb[d.storage_class_id] = delta.storage_gb.get(d.storage_class_id, 0) + d.size_gb
    check_quota(db, tenant, delta)
    if cpu_mem_change:
        params["old_vcpu"], params["old_memory_mb"] = vm.vcpu, vm.memory_mb
        if body.vcpu:
            vm.vcpu = body.vcpu
            params["vcpu"] = body.vcpu
        if body.memory_mb:
            vm.memory_mb = body.memory_mb + (body.memory_mb % 2)
            params["memory_mb"] = vm.memory_mb
    ha = cluster.kind == "cluster" and cluster.ha_enabled
    next_disk = max([d.position for d in vm.disks] + [-1]) + 1
    for d in body.add_disks:
        ds = _pick_datastore(db, cluster.id, d.storage_class_id, ha)
        disk = VMDisk(vm_id=vm.id, datastore_id=ds.id, storage_class_id=d.storage_class_id, size_gb=d.size_gb,
                      position=next_disk)
        next_disk += 1
        db.add(disk)
        db.flush()
        params["new_disk_ids"].append(str(disk.id))
    next_nic = max([n.position for n in vm.nics] + [-1]) + 1
    for op in body.nics:
        net = _tenant_network(db, p, op.network_id) if op.network_id else None
        if op.op == "add":
            if vm.generation == 1 and vm.power_state != "Off":
                raise Conflict("vm_must_be_off", "Generation 1 VMs must be off to add network adapters")
            nic = VMNic(vm_id=vm.id, network_id=net.id if net else None, position=next_nic, hv_name=f"nic{next_nic}")
            next_nic += 1
            db.add(nic)
            db.flush()
            params["nic_ops"].append({"op": "add", "nic_id": str(nic.id), "network_id": str(net.id) if net else None})
        else:
            nic = db.get(VMNic, op.nic_id) if op.nic_id else None
            if nic is None or nic.vm_id != vm.id:
                raise NotFound("nic")
            if op.op == "remove" and vm.generation == 1 and vm.power_state != "Off":
                raise Conflict("vm_must_be_off", "Generation 1 VMs must be off to remove network adapters")
            params["nic_ops"].append({"op": op.op, "nic_id": str(nic.id), "hv_name": nic.hv_name,
                                      "network_id": str(net.id) if net else None})
    if not (cpu_mem_change or params["new_disk_ids"] or params["nic_ops"]):
        raise AppError("nothing_to_change", "No changes requested")
    task = _vm_task(db, p, vm, "vm.reconfigure", params)
    log_event(db, "vm.reconfigure", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip,
              details=body.model_dump(mode="json"))
    db.commit()
    return task_to_dict(task)


@router.post("/vms/{vm_id}/console")
def console_ticket(vm_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    vm = get_vm_for(db, p, vm_id, "vm.console")
    _require_ready(vm)
    if vm.power_state not in ("Running", "Paused"):
        raise Conflict("vm_not_running", "Console is available only for a running VM")
    ttl = int(get_setting(db, "console").get("ticket_ttl_s", 60))
    ticket = new_token(24)
    db.add(ConsoleTicket(token_hash=hash_token(ticket), user_id=p.user.id, vm_id=vm.id,
                         expires_at=utcnow() + dt.timedelta(seconds=ttl)))
    log_event(db, "vm.console", user=p.user, target_type="vm", target_id=vm.id, target_name=vm.name, ip=p.ip)
    db.commit()
    return {"ticket": ticket, "vm": vm.name, "expires_in": ttl}


@router.get("/vms/{vm_id}/access")
def vm_access(vm_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    vm = get_vm_for(db, p, vm_id)
    require_tenant_perm(p, "user.view")
    users = db.scalars(select(User).where(User.tenant_id == p.tenant_id).order_by(User.username)).all()
    out = []
    for u in users:
        roles = [r.role for r in u.roles if r.vm_id == vm.id]
        inherited = [r.role for r in u.roles if r.vm_id is None]
        if roles or inherited:
            out.append({"user_id": str(u.id), "username": u.username, "full_name": u.full_name, "roles": roles,
                        "inherited": inherited})
    return out


# ==========================================================================
# Networks (VLAN-backed)
# ==========================================================================


class NetworkIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    description: str = ""
    cidr: str | None = None
    gateway: str | None = None
    dns: str | None = None


class NetworkUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    cidr: str | None = None
    gateway: str | None = None
    dns: str | None = None


@router.get("/networks")
def list_networks(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "network.view")
    rows = db.scalars(select(Network).where(Network.tenant_id == p.tenant_id).options(selectinload(Network.dhcp))
                      .order_by(Network.name)).all()
    counts = dict(db.execute(select(VMNic.network_id, func.count(VMNic.id)).where(
        VMNic.network_id.in_([n.id for n in rows] or [uuid.uuid4()])).group_by(VMNic.network_id)).all())
    routed = set(db.scalars(select(RouterNetwork.network_id).join(Router, Router.id == RouterNetwork.router_id)
                            .where(Router.tenant_id == p.tenant_id)).all())
    return [network_to_dict(n, counts.get(n.id, 0), n.id in routed) for n in rows]


@router.get("/vlans")
def list_vlans(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "network.view")
    used = {n.vlan_id: n.name for n in db.scalars(select(Network).where(Network.tenant_id == p.tenant_id))}
    vlans = db.scalars(select(TenantVlan.vlan_id).where(TenantVlan.tenant_id == p.tenant_id)
                       .order_by(TenantVlan.vlan_id)).all()
    return [{"vlan_id": v, "network": used.get(v)} for v in vlans]


@router.post("/networks")
def create_network(body: NetworkIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "network.manage")
    name = body.name.strip()
    if not NETWORK_NAME_RE.match(name):
        raise AppError("invalid_network_name", "Invalid network name")
    lock_tenant(db, p.tenant_id)
    if db.scalar(select(Network).where(Network.tenant_id == p.tenant_id, func.lower(Network.name) == name.lower())):
        raise Conflict("network_exists", "Network with this name already exists")
    pool = set(db.scalars(select(TenantVlan.vlan_id).where(TenantVlan.tenant_id == p.tenant_id)).all())
    used = set(db.scalars(select(Network.vlan_id).where(Network.tenant_id == p.tenant_id)).all())
    free = sorted(pool - used)
    if body.vlan_id is not None:
        if body.vlan_id not in pool:
            raise AppError("vlan_not_in_pool", "VLAN is not allocated to the organization", vlan=body.vlan_id)
        if body.vlan_id in used:
            raise Conflict("vlan_in_use", "VLAN is already used by another network", vlans=str(body.vlan_id))
        vlan = body.vlan_id
    else:
        if not free:
            raise Conflict("no_free_vlan", "No free VLAN left in the organization pool")
        vlan = free[0]
    cidr = str(netutil.parse_cidr(body.cidr, "cidr")) if body.cidr else None
    if body.gateway:
        if not cidr:
            raise AppError("network_needs_gateway", "Set the subnet (CIDR) together with the gateway", network=name)
        netutil.parse_interface(body.gateway, cidr)
    net = Network(tenant_id=p.tenant_id, name=name, vlan_id=vlan, description=body.description,
                  cidr=cidr, gateway=body.gateway or None, dns=body.dns or None)
    db.add(net)
    log_event(db, "network.created", user=p.user, target_type="network", target_name=name, ip=p.ip,
              details={"vlan": vlan})
    db.commit()
    return network_to_dict(net)


@router.patch("/networks/{net_id}")
def update_network(net_id: uuid.UUID, body: NetworkUpdateIn, p: Principal = Depends(tenant_user),
                   db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "network.manage")
    net = _tenant_network(db, p, net_id)
    if body.name and body.name.strip() != net.name:
        name = body.name.strip()
        if not NETWORK_NAME_RE.match(name):
            raise AppError("invalid_network_name", "Invalid network name")
        if db.scalar(select(Network).where(Network.tenant_id == p.tenant_id, func.lower(Network.name) == name.lower(),
                                           Network.id != net.id)):
            raise Conflict("network_exists", "Network with this name already exists")
        net.name = name
    for field in ("description", "cidr", "gateway", "dns"):
        value = getattr(body, field)
        if value is not None:
            setattr(net, field, value or ("" if field == "description" else None))
    if net.cidr:
        net.cidr = str(netutil.parse_cidr(net.cidr, "cidr"))
    if net.gateway:
        if not net.cidr:
            raise AppError("network_needs_gateway", "Set the subnet (CIDR) together with the gateway",
                           network=net.name)
        netutil.parse_interface(net.gateway, net.cidr)
    rsvc.revalidate_network_dhcp(net)
    r = db.scalar(select(Router).where(Router.tenant_id == p.tenant_id))
    if r is not None and any(rn.network_id == net.id for rn in r.networks):
        db.flush()
        rsvc.validate_networks_for_router(db, p.tenant_id, [rn.network_id for rn in r.networks])
        rsvc.bump(db, r, p.user)
    db.commit()
    return network_to_dict(net)


@router.delete("/networks/{net_id}")
def delete_network(net_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "network.manage")
    net = _tenant_network(db, p, net_id)
    users = db.scalars(select(VM.name).join(VMNic, VMNic.vm_id == VM.id).where(VMNic.network_id == net.id)).all()
    if users:
        raise Conflict("network_in_use", "Network is used by VMs", vms=", ".join(sorted(set(users))))
    if db.scalar(select(RouterNetwork).where(RouterNetwork.network_id == net.id)):
        raise Conflict("network_on_router", "Disconnect the network from the router first")
    rules = rsvc.rules_referencing(db, p.tenant_id, "network", str(net.id))
    if rules:
        raise Conflict("network_in_rules", "The network is used by firewall rules", rules=", ".join(rules))
    log_event(db, "network.deleted", user=p.user, target_type="network", target_name=net.name, ip=p.ip,
              details={"vlan": net.vlan_id})
    db.delete(net)
    db.commit()
    return {"ok": True}


# ==========================================================================
# ISO images
# ==========================================================================


class IsoInitIn(BaseModel):
    name: str = Field(min_length=5, max_length=255)
    size_bytes: int = Field(gt=0)
    description: str = ""


@router.get("/isos")
def list_isos(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "iso.view")
    rows = db.scalars(select(Iso).where(or_(Iso.tenant_id == p.tenant_id, Iso.tenant_id.is_(None)))
                      .order_by(Iso.tenant_id.is_(None), Iso.name)).all()
    return [iso_to_dict(i) for i in rows if i.tenant_id is not None or i.status == "ready"]


@router.post("/isos")
def init_iso(body: IsoInitIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "iso.manage")
    iso = iso_svc.init_upload(db, p.tenant, p.user, body.name, body.size_bytes, body.description)
    log_event(db, "iso.upload_started", user=p.user, target_type="iso", target_id=iso.id, target_name=iso.name,
              ip=p.ip, details={"file": iso.file_name, "size": iso.size_bytes})
    db.commit()
    return iso_to_dict(iso)


@router.put("/isos/{iso_id}/data")
async def upload_chunk(iso_id: uuid.UUID, offset: int, request: Request, p: Principal = Depends(tenant_user),
                       db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "iso.manage")
    iso = db.get(Iso, iso_id)
    if iso is None or iso.tenant_id != p.tenant_id:
        raise NotFound("iso")
    data = await request.body()

    def _write() -> dict:
        i = iso_svc.write_chunk(db, iso_id, offset, data)
        if i.status == "ready":
            log_event(db, "iso.uploaded", user=p.user, target_type="iso", target_id=i.id, target_name=i.name, ip=p.ip)
        db.commit()
        return iso_to_dict(i)

    return await run_in_threadpool(_write)


@router.delete("/isos/{iso_id}")
def delete_iso(iso_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "iso.manage")
    iso = db.get(Iso, iso_id)
    if iso is None or iso.tenant_id != p.tenant_id:
        raise NotFound("iso")
    iso_svc.delete_iso(db, iso)
    log_event(db, "iso.deleted", user=p.user, target_type="iso", target_id=iso_id, target_name=iso.name, ip=p.ip)
    db.commit()
    return {"ok": True}


# ==========================================================================
# Users and roles (local user database of the organization)
# ==========================================================================


class RoleIn(BaseModel):
    role: Literal["tenant_admin", "audit", "console"]
    vm_id: uuid.UUID | None = None


class TenantUserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    full_name: str = ""
    email: str = ""
    is_active: bool = True
    roles: list[RoleIn] = []


class TenantUserUpdateIn(BaseModel):
    username: str | None = None
    password: str | None = None
    full_name: str | None = None
    email: str | None = None
    is_active: bool | None = None
    unlock: bool | None = None
    roles: list[RoleIn] | None = None


def _get_user(db: Session, p: Principal, user_id: uuid.UUID) -> User:
    u = db.get(User, user_id)
    if u is None or u.tenant_id != p.tenant_id:
        raise NotFound("user")
    return u


def _roles_payload(roles: list[RoleIn]) -> list[dict]:
    return [{"role": r.role, "vm_id": str(r.vm_id) if r.vm_id else None} for r in roles]


def _ensure_admin_left(db: Session, p: Principal, user: User, roles: list[RoleIn] | None, active: bool | None) -> None:
    still_admin = (active is not False and user.is_active) and (
        any(r.role == "tenant_admin" and r.vm_id is None for r in roles) if roles is not None
        else any(r.role == "tenant_admin" and r.vm_id is None for r in user.roles))
    if not still_admin and user_svc.count_tenant_admins(db, p.tenant_id, exclude_user=user.id) == 0:
        raise Conflict("last_tenant_admin", "The organization must keep at least one tenant administrator")


@router.get("/users")
def list_users(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "user.view")
    return [user_to_dict(u) for u in db.scalars(select(User).where(User.tenant_id == p.tenant_id)
                                                .order_by(User.username))]


@router.post("/users")
def create_user(body: TenantUserIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "user.manage")
    u = user_svc.create_user(db, p.tenant_id, body.model_dump(exclude={"roles"}))
    user_svc.set_roles(db, u, _roles_payload(body.roles))
    log_event(db, "user.created", user=p.user, target_type="user", target_id=u.id, target_name=u.username, ip=p.ip,
              details={"roles": _roles_payload(body.roles)})
    db.commit()
    db.refresh(u)
    return user_to_dict(u)


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: TenantUserUpdateIn, p: Principal = Depends(tenant_user),
                db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "user.manage")
    u = _get_user(db, p, user_id)
    _ensure_admin_left(db, p, u, body.roles, body.is_active)
    user_svc.update_user(db, u, body.model_dump(exclude_unset=True, exclude={"roles"}))
    if body.roles is not None:
        user_svc.set_roles(db, u, _roles_payload(body.roles))
    log_event(db, "user.updated", user=p.user, target_type="user", target_id=u.id, target_name=u.username, ip=p.ip,
              details={"roles": _roles_payload(body.roles) if body.roles is not None else None})
    db.commit()
    db.refresh(u)
    return user_to_dict(u)


@router.delete("/users/{user_id}")
def delete_user(user_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "user.manage")
    u = _get_user(db, p, user_id)
    if u.id == p.user.id:
        raise AppError("cannot_delete_self", "You cannot delete your own account")
    _ensure_admin_left(db, p, u, [], False)
    log_event(db, "user.deleted", user=p.user, target_type="user", target_id=u.id, target_name=u.username, ip=p.ip)
    db.delete(u)
    db.commit()
    return {"ok": True}


def ensure_visible_tasks(p: Principal) -> None:  # pragma: no cover - helper for symmetry
    if not (p.has_tenant_perm("task.view") or p.scoped_vm_ids()):
        raise Forbidden()
