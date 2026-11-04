"""Background operations executed by the worker (VM lifecycle, cluster refresh)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..hyperv import HyperVDriver, HyperVError
from ..models import VM, Cluster, Host, Iso, Network, Snapshot, Task, VMDisk, VMNic
from .audit import log_event
from .inventory import UP_STATES, apply_vm_summary, endpoint_for, refresh_cluster, run_on_vm, sync_vms
from .isostore import IsoStore
from .settings import get_setting

log = logging.getLogger(__name__)


class TaskContext:
    def __init__(self, task_id: uuid.UUID, progress_cb: Callable[[uuid.UUID, int, str], None]) -> None:
        self.task_id = task_id
        self._cb = progress_cb

    def progress(self, pct: int, message: str = "") -> None:
        self._cb(self.task_id, pct, message)


Handler = Callable[[Session, HyperVDriver, Task, TaskContext], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}
ERROR_HOOKS: dict[str, Callable[[Session, HyperVDriver, Task, Exception], None]] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def on_error(kind: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    def deco(fn: Callable[..., None]) -> Callable[..., None]:
        ERROR_HOOKS[kind] = fn
        return fn

    return deco


# --------------------------------------------------------------------------
# Paths and placement
# --------------------------------------------------------------------------


def vm_folder(vm: VM, datastore_path: str) -> str:
    return f"{datastore_path.rstrip(chr(92))}\\{vm.cluster.vm_folder}\\{vm.tenant.slug}\\{vm.hv_name}"


def vm_base(vm: VM, datastore_path: str) -> str:
    """Folder passed to New-VM -Path (Hyper-V adds <VM name> below it)."""
    return f"{datastore_path.rstrip(chr(92))}\\{vm.cluster.vm_folder}\\{vm.tenant.slug}"


def disk_path(vm: VM, disk: VMDisk) -> str:
    return f"{vm_folder(vm, disk.datastore.path)}\\Virtual Hard Disks\\{vm.hv_name}_disk{disk.position}.vhdx"


def vm_folders(vm: VM) -> list[str]:
    folders = {vm_folder(vm, d.datastore.path) for d in vm.disks if d.datastore is not None}
    return sorted(folders)


def choose_host(db: Session, driver: HyperVDriver, cluster: Cluster, memory_mb: int) -> Host:
    """Automatic placement: the Up node with the most free memory (live query)."""
    reserve = int(get_setting(db, "placement").get("memory_reserve_mb", 4096))
    candidates = [h for h in cluster.hosts if h.in_cluster and not h.maintenance and
                  (h.state in UP_STATES or cluster.kind == "standalone")]
    best: Host | None = None
    best_free = -1
    errors = []
    for h in candidates:
        try:
            mem = driver.host_memory(endpoint_for(cluster, h))
        except HyperVError as exc:
            errors.append(str(exc))
            h.reachable = False
            continue
        h.memory_free_mb = mem.get("memory_free_mb")
        h.memory_total_mb = mem.get("memory_total_mb") or h.memory_total_mb
        h.reachable = True
        free = (h.memory_free_mb or 0) - reserve
        if free > best_free:
            best, best_free = h, free
    if best is None:
        raise AppError("no_host_available", "No reachable host available for placement: " + "; ".join(errors))
    log.info("Placement for %s MB: %s (free %s MB after reserve)", memory_mb, best.name, best_free)
    return best


def _iso_path(db: Session, iso: Iso | None) -> str | None:
    if iso is None:
        return None
    return IsoStore.from_setting(get_setting(db, "iso_share")).host_path(iso.file_name)


def _vm(db: Session, task: Task) -> VM:
    vm = db.get(VM, task.target_id)
    if vm is None:
        raise AppError("not_found", "VM no longer exists")
    return vm


# --------------------------------------------------------------------------
# VM create
# --------------------------------------------------------------------------


@handler("vm.create")
def vm_create(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    cluster = vm.cluster
    ctx.progress(5, "placement")
    host = choose_host(db, driver, cluster, vm.memory_mb)
    vm.host_id = host.id
    db.commit()
    ctx.progress(15, f"host {host.name}")

    first_ds = vm.disks[0].datastore if vm.disks else None
    if first_ds is None:
        raise AppError("no_datastore", "VM has no disk/datastore")
    for d in vm.disks:
        d.path = disk_path(vm, d)
    nics = []
    for nic in vm.nics:
        nic.hv_name = f"nic{nic.position}"
        nics.append({"name": nic.hv_name, "switch": cluster.vswitch_name or None,
                     "vlan": nic.network.vlan_id if nic.network else None})
    spec = {
        "name": vm.hv_name,
        "generation": vm.generation,
        "vcpu": vm.vcpu,
        "memory_mb": vm.memory_mb,
        "path": vm_base(vm, first_ds.path),
        "notes": f"HV Director | tenant: {vm.tenant.slug} | vm: {vm.name} | id: {vm.id}",
        "secure_boot": vm.secure_boot,
        "iso": _iso_path(db, vm.iso),
        "disks": [{"path": d.path, "size_gb": d.size_gb} for d in vm.disks],
        "nics": nics,
    }
    ep = endpoint_for(cluster, host)
    res = driver.create_vm(ep, spec)
    vm.hv_id = str(res["id"]).upper()
    vm.power_state = res.get("state") or "Off"
    macs = {n["name"]: n.get("mac") for n in res.get("nics", [])}
    for nic in vm.nics:
        nic.mac = macs.get(nic.hv_name)
    db.commit()
    ctx.progress(60, "created")

    if cluster.kind == "cluster" and cluster.ha_enabled:
        ctx.progress(70, "high availability")
        ha = driver.make_ha(ep, vm.hv_id, vm.hv_name)
        vm.cluster_group = ha.get("group") or vm.hv_name
        db.commit()

    if task.params.get("power_on"):
        ctx.progress(85, "power on")
        s = driver.vm_power(ep, vm.hv_id, "start")
        apply_vm_summary(vm, s, host)
    vm.status = "ready"
    vm.last_error = ""
    log_event(db, "vm.created", username=task.username, tenant_id=vm.tenant_id, target_type="vm",
              target_id=vm.id, target_name=vm.name, details={"host": host.name})
    return {"vm_id": str(vm.id), "host": host.name, "hv_id": vm.hv_id, "group": vm.cluster_group}


@on_error("vm.create")
def vm_create_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    vm = db.get(VM, task.target_id)
    if vm is None:
        return
    # Roll back whatever was created on the host.
    if vm.hv_id and vm.host is not None:
        try:
            driver.delete_vm(endpoint_for(vm.cluster, vm.host), {
                "id": vm.hv_id, "group": vm.cluster_group,
                "disk_paths": [d.path for d in vm.disks if d.path],
                "folders": vm_folders(vm)})
            vm.hv_id = None
            vm.cluster_group = None
        except HyperVError as cleanup_exc:
            log.warning("Rollback of %s failed: %s", vm.hv_name, cleanup_exc)
    vm.status = "error"
    vm.power_state = "Unknown"
    vm.last_error = str(exc)[:4000]
    log_event(db, "vm.created", username=task.username, tenant_id=vm.tenant_id, target_type="vm",
              target_id=vm.id, target_name=vm.name, status="failure", details={"error": str(exc)[:1000]})


# --------------------------------------------------------------------------
# Power / delete / refresh
# --------------------------------------------------------------------------


@handler("vm.power")
def vm_power(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    action = task.params["action"]
    s, host = run_on_vm(db, driver, vm, lambda ep: driver.vm_power(ep, vm.hv_id, action))
    apply_vm_summary(vm, s, host)
    return {"state": vm.power_state, "host": host.name}


@handler("vm.refresh")
def vm_refresh(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    info, host = run_on_vm(db, driver, vm, lambda ep: driver.vm_info(ep, vm.hv_id))
    apply_vm_summary(vm, info, host)
    _merge_snapshots(db, vm, info.get("snapshots") or [])
    return {"state": vm.power_state, "host": host.name}


def _merge_snapshots(db: Session, vm: VM, snaps: list[dict[str, Any]]) -> None:
    """Drop DB snapshots that no longer exist on the host (deleted outside the portal)."""
    present = {str(s["id"]).upper() for s in snaps}
    for s in list(vm.snapshots):
        if s.status == "ready" and s.hv_id and s.hv_id.upper() not in present:
            db.delete(s)


def delete_vm_resources(db: Session, driver: HyperVDriver, vm: VM) -> None:
    """Remove the VM, its cluster role, disks and folders from Hyper-V."""
    args = {"id": vm.hv_id, "group": vm.cluster_group, "disk_paths": [d.path for d in vm.disks if d.path],
            "folders": vm_folders(vm)}
    if not vm.hv_id:
        return
    try:
        run_on_vm(db, driver, vm, lambda ep: driver.delete_vm(ep, args))
    except HyperVError as exc:
        if exc.code != "VM_NOT_FOUND":
            raise
        # VM vanished from Hyper-V: clean up files from any node.
        host = next((h for h in vm.cluster.hosts if h.in_cluster and h.state in UP_STATES), None)
        if host is not None:
            driver.delete_vm(endpoint_for(vm.cluster, host), dict(args, id=None))


@handler("vm.delete")
def vm_delete(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    delete_vm_resources(db, driver, vm)
    name, tenant_id, vm_id = vm.name, vm.tenant_id, vm.id
    db.delete(vm)
    log_event(db, "vm.deleted", username=task.username, tenant_id=tenant_id, target_type="vm", target_id=vm_id,
              target_name=name)
    return {"deleted": True}


@on_error("vm.delete")
def vm_delete_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    vm = db.get(VM, task.target_id)
    if vm is not None:
        vm.status = "error"
        vm.last_error = str(exc)[:4000]


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------


@handler("vm.snapshot.create")
def snapshot_create(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    snap = db.get(Snapshot, uuid.UUID(task.params["snapshot_id"]))
    if snap is None:
        raise AppError("not_found", "snapshot record vanished")
    res, _ = run_on_vm(db, driver, vm, lambda ep: driver.snapshot(ep, vm.hv_id, "create", name=snap.name))
    snap.hv_id = str(res["id"]).upper()
    snap.status = "ready"
    return {"snapshot_id": str(snap.id), "hv_id": snap.hv_id}


@on_error("vm.snapshot.create")
def snapshot_create_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    snap = db.get(Snapshot, uuid.UUID(task.params["snapshot_id"]))
    if snap is not None:
        db.delete(snap)


@handler("vm.snapshot.revert")
def snapshot_revert(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    snap = db.get(Snapshot, uuid.UUID(task.params["snapshot_id"]))
    if snap is None or not snap.hv_id:
        raise AppError("not_found", "snapshot not found")
    res, _ = run_on_vm(db, driver, vm, lambda ep: driver.snapshot(ep, vm.hv_id, "revert", snapshot_id=snap.hv_id))
    vm.power_state = res.get("state") or vm.power_state
    return {"state": vm.power_state}


@handler("vm.snapshot.delete")
def snapshot_delete(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    snap = db.get(Snapshot, uuid.UUID(task.params["snapshot_id"]))
    if snap is None:
        return {"deleted": False}
    if snap.hv_id:
        run_on_vm(db, driver, vm, lambda ep: driver.snapshot(ep, vm.hv_id, "delete", snapshot_id=snap.hv_id))
    db.delete(snap)
    return {"deleted": True}


@on_error("vm.snapshot.delete")
def snapshot_delete_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    snap = db.get(Snapshot, uuid.UUID(task.params["snapshot_id"]))
    if snap is not None:
        snap.status = "ready"


# --------------------------------------------------------------------------
# Media (ISO)
# --------------------------------------------------------------------------


@handler("vm.media")
def vm_media(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    iso = db.get(Iso, uuid.UUID(task.params["iso_id"])) if task.params.get("iso_id") else None
    if task.params.get("iso_id") and iso is None:
        raise AppError("not_found", "ISO not found")
    path = _iso_path(db, iso)
    run_on_vm(db, driver, vm, lambda ep: driver.set_media(ep, vm.hv_id, path))
    vm.iso_id = iso.id if iso else None
    return {"iso": iso.name if iso else None}


# --------------------------------------------------------------------------
# Reconfigure (vCPU/RAM, add disk, NIC changes)
# --------------------------------------------------------------------------


@handler("vm.reconfigure")
def vm_reconfigure(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    vm = _vm(db, task)
    p = task.params
    changes: dict[str, Any] = {"add_disks": [], "nics": []}
    if p.get("vcpu"):
        changes["vcpu"] = p["vcpu"]
    if p.get("memory_mb"):
        changes["memory_mb"] = p["memory_mb"]
    new_disks = [db.get(VMDisk, uuid.UUID(d)) for d in p.get("new_disk_ids", [])]
    for d in new_disks:
        if d is None:
            continue
        d.path = disk_path(vm, d)
        changes["add_disks"].append({"path": d.path, "size_gb": d.size_gb})
    for n in p.get("nic_ops", []):
        nic = db.get(VMNic, uuid.UUID(n["nic_id"])) if n.get("nic_id") else None
        net = db.get(Network, uuid.UUID(n["network_id"])) if n.get("network_id") else None
        switch = vm.cluster.vswitch_name or None
        if n["op"] == "add" and nic is not None:
            nic.hv_name = nic.hv_name or f"nic{nic.position}"
            changes["nics"].append({"op": "add", "name": nic.hv_name, "switch": switch if net else None,
                                    "vlan": net.vlan_id if net else None})
        elif n["op"] == "set" and nic is not None:
            changes["nics"].append({"op": "set", "name": nic.hv_name, "switch": switch if net else None,
                                    "vlan": net.vlan_id if net else None})
        elif n["op"] == "remove":
            changes["nics"].append({"op": "remove", "name": n["hv_name"]})
    res, host = run_on_vm(db, driver, vm, lambda ep: driver.reconfigure(ep, vm.hv_id, changes))
    macs = {x["name"]: x.get("mac") for x in res.get("nics", [])}
    for nic in vm.nics:
        if nic.hv_name in macs:
            nic.mac = macs[nic.hv_name]
    for n in p.get("nic_ops", []):
        nic = db.get(VMNic, uuid.UUID(n["nic_id"])) if n.get("nic_id") else None
        if nic is None:
            continue
        if n["op"] == "set":
            nic.network_id = uuid.UUID(n["network_id"]) if n.get("network_id") else None
        elif n["op"] == "remove":
            db.delete(nic)
    apply_vm_summary(vm, res, host)
    return {"ok": True}


@on_error("vm.reconfigure")
def vm_reconfigure_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    """Undo the quota reservation made by the API (values/rows created up-front)."""
    vm = db.get(VM, task.target_id)
    if vm is None:
        return
    p = task.params
    if p.get("old_vcpu"):
        vm.vcpu = p["old_vcpu"]
    if p.get("old_memory_mb"):
        vm.memory_mb = p["old_memory_mb"]
    for d in p.get("new_disk_ids", []):
        disk = db.get(VMDisk, uuid.UUID(d))
        if disk is not None:
            db.delete(disk)
    for n in p.get("nic_ops", []):
        if n["op"] == "add" and n.get("nic_id"):
            nic = db.get(VMNic, uuid.UUID(n["nic_id"]))
            if nic is not None:
                db.delete(nic)
    vm.last_error = str(exc)[:4000]


# --------------------------------------------------------------------------
# Cluster refresh / sync
# --------------------------------------------------------------------------


@handler("cluster.refresh")
def cluster_refresh(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    cluster = db.get(Cluster, task.target_id)
    if cluster is None:
        raise AppError("not_found", "cluster not found")
    report = refresh_cluster(db, driver, cluster)
    log_event(db, "cluster.refreshed", username=task.username, target_type="cluster", target_id=cluster.id,
              target_name=cluster.name, details={k: report[k] for k in ("added", "removed", "returned")})
    return report


@handler("cluster.sync")
def cluster_sync(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    cluster = db.get(Cluster, task.target_id)
    if cluster is None:
        raise AppError("not_found", "cluster not found")
    return sync_vms(db, driver, cluster)


def sync_all(db: Session, driver: HyperVDriver) -> None:
    for cluster in db.scalars(select(Cluster)).all():
        try:
            sync_vms(db, driver, cluster)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("Sync of %s failed: %s", cluster.name, exc)
