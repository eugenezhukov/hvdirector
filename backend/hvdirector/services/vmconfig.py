"""VM reconfiguration shared by the tenant and provider APIs.

The API validates the request, checks the tenant quota and reserves the
resources in the database up front (new vCPU/RAM values, new and extended
disk sizes, new adapters); the ``vm.reconfigure`` task applies the change in
Hyper-V and the task error hook undoes the reservation.  Removed disks and
adapters stay in the database (and in the quota) until the task succeeds.

Hyper-V rules enforced here:

* vCPU count: VM must be off (no CPU hot-add).
* Memory (static): off, or running (runtime memory resize of Windows Server
  2016+ hosts; the guest must support it, otherwise the task fails and the
  reservation is rolled back).
* Disks: extend only (VHDX, no shrink), up to 64 TB; not while the VM has
  checkpoints; the IDE system disk of a generation 1 VM only when off;
  the system (boot) disk cannot be removed.  Data disks are on SCSI and can
  be extended / removed while the VM runs.
* Network adapters: generation 1 VMs must be off to add or remove adapters.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import AppError, Conflict, NotFound
from ..models import VM, Datastore, Network, StorageClass, Tenant, TenantStorageQuota, VMDisk, VMNic
from .quotas import Delta, check_quota, lock_tenant, quota_report

MAX_DISK_GB = 65536  # VHDX limit: 64 TB
MAX_NICS = 8


def pick_datastore(db: Session, cluster_id: uuid.UUID, storage_class_id: uuid.UUID, ha: bool) -> Datastore:
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


def tenant_storage_classes(db: Session, tenant: Tenant) -> list[dict[str, Any]]:
    """Storage classes allowed to a tenant with quota usage and whether its cluster has a datastore of the class."""
    report = {s["storage_class_id"]: s for s in quota_report(db, tenant)["storage"]}
    out = []
    for q in db.scalars(select(TenantStorageQuota).where(TenantStorageQuota.tenant_id == tenant.id)):
        sc = db.get(StorageClass, q.storage_class_id)
        if sc is None:
            continue
        has_ds = db.scalar(select(Datastore.id).where(
            Datastore.cluster_id == tenant.cluster_id, Datastore.storage_class_id == sc.id,
            Datastore.enabled.is_(True), Datastore.present.is_(True)).limit(1))
        out.append({"id": str(sc.id), "name": sc.name, "description": sc.description, "tier": sc.tier,
                    "limit_gb": q.limit_gb, "used_gb": report.get(str(sc.id), {}).get("used_gb", 0),
                    "available": has_ds is not None})
    out.sort(key=lambda x: (x["tier"], x["name"]))
    return out


def tenant_network_lookup(db: Session, tenant_id: uuid.UUID) -> Callable[[uuid.UUID], Network]:
    def lookup(network_id: uuid.UUID) -> Network:
        net = db.get(Network, network_id)
        if net is None or net.tenant_id != tenant_id:
            raise NotFound("network")
        return net

    return lookup


def plan_reconfigure(db: Session, vm: VM, body: Any) -> dict[str, Any]:
    """Validate a reconfiguration request, check quotas and reserve resources; returns the task parameters.

    ``body`` has the attributes vcpu, memory_mb, add_disks [{size_gb, storage_class_id}],
    disk_ops [{op: resize|remove, disk_id, size_gb}] and nics [{op: add|set|remove, nic_id, network_id}].
    """
    if vm.kind != "vm":
        raise Conflict("vm_kind_not_supported", "Router VMs are configured from the router settings")
    if vm.status != "ready" or not vm.hv_id:
        raise Conflict("vm_not_ready", "VM is not in a ready state", status=vm.status)
    tenant = lock_tenant(db, vm.tenant_id)
    cluster = vm.cluster
    network = tenant_network_lookup(db, vm.tenant_id)
    off = vm.power_state == "Off"
    params: dict[str, Any] = {"new_disk_ids": [], "nic_ops": [], "resize_disks": [], "remove_disk_ids": []}
    delta = Delta()

    vcpu = body.vcpu if body.vcpu and body.vcpu != vm.vcpu else None
    memory_mb = body.memory_mb + (body.memory_mb % 2) if body.memory_mb else None
    if memory_mb == vm.memory_mb:
        memory_mb = None
    if vcpu:
        if not off:
            raise Conflict("vm_must_be_off", "Power off the VM to change the number of vCPUs")
        delta.vcpu = vcpu - vm.vcpu
    if memory_mb:
        if vm.power_state not in ("Off", "Running"):
            raise Conflict("vm_must_be_off", "Memory can be changed when the VM is off or running")
        delta.memory_mb = memory_mb - vm.memory_mb

    disks = {d.id: d for d in vm.disks}
    resize: list[tuple[VMDisk, int]] = []
    seen: set[uuid.UUID] = set()
    for op in body.disk_ops:
        d = disks.get(op.disk_id)
        if d is None:
            raise NotFound("disk")
        if d.id in seen:
            raise AppError("duplicate_disk_op", "One change per disk in a request")
        seen.add(d.id)
        if vm.snapshots:
            raise Conflict("vm_has_snapshots", "Delete the VM snapshots before changing its disks")
        system = d.position == 0
        if op.op == "resize":
            if not op.size_gb or op.size_gb <= d.size_gb:
                raise AppError("disk_shrink", "A disk can only be extended", size_gb=d.size_gb)
            if op.size_gb > MAX_DISK_GB:
                raise AppError("disk_too_large", "Disk is too large", max=MAX_DISK_GB)
            if vm.generation == 1 and system and not off:
                raise Conflict("vm_must_be_off", "Power off the VM to extend the IDE system disk of a generation 1 VM")
            delta.storage_gb[d.storage_class_id] = delta.storage_gb.get(d.storage_class_id, 0) + op.size_gb - d.size_gb
            resize.append((d, op.size_gb))
        else:
            if system:
                raise Conflict("system_disk", "The system (boot) disk cannot be removed")
            params["remove_disk_ids"].append(str(d.id))
    for d in body.add_disks:
        delta.storage_gb[d.storage_class_id] = delta.storage_gb.get(d.storage_class_id, 0) + d.size_gb
    if len(vm.disks) + len(body.add_disks) - len(params["remove_disk_ids"]) > 64:
        raise AppError("too_many_disks", "Too many disks", max=64)

    nic_count = len(vm.nics)
    for op in body.nics:
        if op.op == "add":
            nic_count += 1
        elif op.op == "remove":
            nic_count -= 1
        if op.op in ("add", "remove") and vm.generation == 1 and not off:
            raise Conflict("vm_must_be_off", "Generation 1 VMs must be off to add or remove network adapters")
    if nic_count > MAX_NICS:
        raise AppError("too_many_nics", "Too many network adapters", max=MAX_NICS)

    check_quota(db, tenant, delta)

    # reservations (undone by the task error hook)
    if vcpu or memory_mb:
        params["old_vcpu"], params["old_memory_mb"] = vm.vcpu, vm.memory_mb
    if vcpu:
        vm.vcpu = params["vcpu"] = vcpu
    if memory_mb:
        vm.memory_mb = params["memory_mb"] = memory_mb
    for d, size_gb in resize:
        params["resize_disks"].append({"disk_id": str(d.id), "old_size_gb": d.size_gb, "size_gb": size_gb})
        d.size_gb = size_gb
    ha = cluster.kind == "cluster" and cluster.ha_enabled
    next_disk = max([d.position for d in vm.disks] + [-1]) + 1
    for nd in body.add_disks:
        ds = pick_datastore(db, cluster.id, nd.storage_class_id, ha)
        disk = VMDisk(vm_id=vm.id, datastore_id=ds.id, storage_class_id=nd.storage_class_id, size_gb=nd.size_gb,
                      position=next_disk)
        next_disk += 1
        db.add(disk)
        db.flush()
        params["new_disk_ids"].append(str(disk.id))
    next_nic = max([n.position for n in vm.nics] + [-1]) + 1
    for op in body.nics:
        net = network(op.network_id) if op.network_id else None
        if op.op == "add":
            nic = VMNic(vm_id=vm.id, network_id=net.id if net else None, position=next_nic, hv_name=f"nic{next_nic}")
            next_nic += 1
            db.add(nic)
            db.flush()
            params["nic_ops"].append({"op": "add", "nic_id": str(nic.id), "network_id": str(net.id) if net else None})
        else:
            nic = db.get(VMNic, op.nic_id) if op.nic_id else None
            if nic is None or nic.vm_id != vm.id:
                raise NotFound("nic")
            params["nic_ops"].append({"op": op.op, "nic_id": str(nic.id), "hv_name": nic.hv_name,
                                      "network_id": str(net.id) if net else None})
    if not (vcpu or memory_mb or params["new_disk_ids"] or params["nic_ops"] or params["resize_disks"]
            or params["remove_disk_ids"]):
        raise AppError("nothing_to_change", "No changes requested")
    return params


__all__ = ["MAX_DISK_GB", "MAX_NICS", "pick_datastore", "plan_reconfigure", "tenant_network_lookup",
           "tenant_storage_classes"]
