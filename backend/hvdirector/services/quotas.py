"""Tenant resource quotas: vCPU, RAM, VM count, disk per storage class, ISO volume, snapshots."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import AppError, QuotaExceeded
from ..models import VM, Iso, Snapshot, StorageClass, Tenant, TenantStorageQuota, VMDisk

GB = 1024**3


@dataclass
class Delta:
    vcpu: int = 0
    memory_mb: int = 0
    vms: int = 0
    snapshots: int = 0
    iso_bytes: int = 0
    storage_gb: dict[uuid.UUID, int] = field(default_factory=dict)


def lock_tenant(db: Session, tenant_id: uuid.UUID) -> Tenant:
    """Serialise quota checks of one tenant (row lock until commit)."""
    tenant = db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update()).scalar_one()
    return tenant


def tenant_usage(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    vcpu, mem, count = db.execute(
        select(func.coalesce(func.sum(VM.vcpu), 0), func.coalesce(func.sum(VM.memory_mb), 0), func.count(VM.id))
        .where(VM.tenant_id == tenant_id)
    ).one()
    storage_rows = db.execute(
        select(VMDisk.storage_class_id, func.coalesce(func.sum(VMDisk.size_gb), 0))
        .join(VM, VM.id == VMDisk.vm_id)
        .where(VM.tenant_id == tenant_id)
        .group_by(VMDisk.storage_class_id)
    ).all()
    snapshots = db.scalar(select(func.count(Snapshot.id)).where(Snapshot.tenant_id == tenant_id)) or 0
    iso_bytes = db.scalar(select(func.coalesce(func.sum(Iso.size_bytes), 0)).where(Iso.tenant_id == tenant_id)) or 0
    return {
        "vcpu": int(vcpu),
        "memory_mb": int(mem),
        "vms": int(count),
        "snapshots": int(snapshots),
        "iso_bytes": int(iso_bytes),
        "storage_gb": {sc_id: int(total) for sc_id, total in storage_rows},
    }


def check_quota(db: Session, tenant: Tenant, delta: Delta) -> None:
    usage = tenant_usage(db, tenant.id)

    def _check(resource: str, limit: int | None, used: int, requested: int) -> None:
        if requested <= 0 or limit is None:
            return
        if used + requested > limit:
            raise QuotaExceeded(resource, limit, used, requested)

    _check("vcpu", tenant.quota_vcpu, usage["vcpu"], delta.vcpu)
    _check("memory_mb", tenant.quota_memory_mb, usage["memory_mb"], delta.memory_mb)
    _check("vms", tenant.quota_vms, usage["vms"], delta.vms)
    _check("snapshots", tenant.quota_snapshots, usage["snapshots"], delta.snapshots)
    if delta.iso_bytes > 0 and tenant.quota_iso_gb is not None:
        limit = tenant.quota_iso_gb * GB
        if usage["iso_bytes"] + delta.iso_bytes > limit:
            raise QuotaExceeded("iso_gb", tenant.quota_iso_gb, round(usage["iso_bytes"] / GB, 2),
                                round(delta.iso_bytes / GB, 2))
    if delta.storage_gb:
        quotas = {
            q.storage_class_id: q
            for q in db.scalars(select(TenantStorageQuota).where(TenantStorageQuota.tenant_id == tenant.id))
        }
        for sc_id, gb in delta.storage_gb.items():
            if gb <= 0:
                continue
            q = quotas.get(sc_id)
            if q is None:
                sc = db.get(StorageClass, sc_id)
                raise AppError("storage_class_not_allowed",
                               f"Storage class {sc.name if sc else sc_id} is not available to the tenant",
                               storage_class=sc.name if sc else str(sc_id))
            if q.limit_gb is not None and usage["storage_gb"].get(sc_id, 0) + gb > q.limit_gb:
                sc = db.get(StorageClass, sc_id)
                raise QuotaExceeded(f"storage:{sc.name if sc else sc_id}", q.limit_gb,
                                    usage["storage_gb"].get(sc_id, 0), gb)


def quota_report(db: Session, tenant: Tenant) -> dict[str, Any]:
    usage = tenant_usage(db, tenant.id)
    storage = []
    for q in db.scalars(select(TenantStorageQuota).where(TenantStorageQuota.tenant_id == tenant.id)):
        sc = db.get(StorageClass, q.storage_class_id)
        storage.append({
            "storage_class_id": str(q.storage_class_id),
            "name": sc.name if sc else "?",
            "tier": sc.tier if sc else 0,
            "limit_gb": q.limit_gb,
            "used_gb": usage["storage_gb"].get(q.storage_class_id, 0),
        })
    storage.sort(key=lambda s: (s["tier"], s["name"]))
    return {
        "vcpu": {"limit": tenant.quota_vcpu, "used": usage["vcpu"]},
        "memory_mb": {"limit": tenant.quota_memory_mb, "used": usage["memory_mb"]},
        "vms": {"limit": tenant.quota_vms, "used": usage["vms"]},
        "snapshots": {"limit": tenant.quota_snapshots, "used": usage["snapshots"]},
        "iso_gb": {"limit": tenant.quota_iso_gb, "used": round(usage["iso_bytes"] / GB, 2)},
        "storage": storage,
    }
