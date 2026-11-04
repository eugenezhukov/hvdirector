"""Provider (system administrator) API."""

from __future__ import annotations

import re
import socket
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AppError, Conflict, NotFound
from ..hyperv import Endpoint, HyperVError, get_driver
from ..models import (VM, Cluster, Datastore, ExternalNetwork, Host, Iso, Network, Router, StorageClass, Task,
                      Tenant, TenantStorageQuota, TenantVlan, User, VMDisk)
from ..security import encrypt_secret
from ..services import isos as iso_svc
from ..services import users as user_svc
from ..services.audit import log_event
from ..services.inventory import discovery_summary
from ..services.quotas import quota_report
from ..services.rbac import Principal
from ..services.settings import DEFAULTS, READONLY_FIELDS, get_setting, public_setting, set_setting
from ..services.taskqueue import enqueue, task_to_dict
from ..utils import SLUG_RE, VlanParseError, parse_vlan_ranges
from .deps import provider_admin
from .serializers import (cluster_to_dict, datastore_to_dict, host_to_dict, iso_to_dict, storage_class_to_dict,
                          tenant_to_dict, user_to_dict, vm_to_dict)

router = APIRouter(prefix="/api/provider", tags=["provider"])

WIN_PATH_RE = re.compile(r"^([A-Za-z]:\\|\\\\[^\\]+\\[^\\]+)")
FOLDER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
WinrmAuth = Literal["ntlm", "negotiate", "kerberos", "credssp", "basic"]


# ==========================================================================
# Dashboard
# ==========================================================================


@router.get("/dashboard")
def dashboard(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    clusters = db.scalars(select(Cluster)).all()
    hosts = [h for c in clusters for h in c.hosts if h.in_cluster]
    vms = db.scalars(select(VM)).all()
    tenants = db.scalars(select(Tenant)).all()
    failed = db.scalars(select(Task).where(Task.status == "error").order_by(Task.created_at.desc()).limit(5)).all()
    return {
        "clusters": len(clusters),
        "clusters_error": sum(1 for c in clusters if c.status == "error"),
        "hosts": len(hosts),
        "hosts_up": sum(1 for h in hosts if h.state in ("Up", "Online")),
        "tenants": len(tenants),
        "vms": len(vms),
        "vms_running": sum(1 for v in vms if v.power_state == "Running"),
        "cpu_total": sum(h.logical_cpus or 0 for h in hosts),
        "memory_total_mb": sum(h.memory_total_mb or 0 for h in hosts),
        "vcpu_allocated": sum(v.vcpu for v in vms),
        "memory_allocated_mb": sum(v.memory_mb for v in vms),
        "failed_tasks": [task_to_dict(t) for t in failed],
    }


# ==========================================================================
# Clusters / hosts / datastores
# ==========================================================================


class ConnectionIn(BaseModel):
    address: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)
    winrm_transport: Literal["http", "https"] = "http"
    winrm_port: int = Field(default=5985, ge=1, le=65535)
    winrm_auth: WinrmAuth = "ntlm"
    winrm_cert_validation: bool = False


class NodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    address: str = Field(min_length=1, max_length=255)
    fqdn: str | None = None


class DatastoreIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=3, max_length=512)
    kind: Literal["csv", "smb", "local"] = "csv"
    storage_class_id: uuid.UUID | None = None
    enabled: bool = True
    discovered: bool = False
    capacity_gb: float | None = None
    free_gb: float | None = None


class ClusterCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    kind: Literal["cluster", "standalone"]
    seed_address: str
    username: str
    password: str
    winrm_transport: Literal["http", "https"] = "http"
    winrm_port: int = Field(default=5985, ge=1, le=65535)
    winrm_auth: WinrmAuth = "ntlm"
    winrm_cert_validation: bool = False
    console_username: str | None = None
    console_password: str | None = None
    console_port: int = Field(default=2179, ge=1, le=65535)
    vswitch_name: str = Field(min_length=1, max_length=128)
    ha_enabled: bool = True
    vm_folder: str = "HVDirector"
    cluster_name: str | None = None
    cluster_fqdn: str | None = None
    cluster_address: str | None = None
    domain: str | None = None
    nodes: list[NodeIn] = Field(min_length=1)
    datastores: list[DatastoreIn] = []


class ClusterUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    username: str | None = None
    password: str | None = None
    winrm_transport: Literal["http", "https"] | None = None
    winrm_port: int | None = Field(default=None, ge=1, le=65535)
    winrm_auth: WinrmAuth | None = None
    winrm_cert_validation: bool | None = None
    console_username: str | None = None
    console_password: str | None = None
    console_port: int | None = Field(default=None, ge=1, le=65535)
    vswitch_name: str | None = None
    ha_enabled: bool | None = None
    vm_folder: str | None = None
    seed_address: str | None = None


class HostUpdateIn(BaseModel):
    address: str | None = None
    maintenance: bool | None = None


class DatastoreUpdateIn(BaseModel):
    name: str | None = None
    path: str | None = None
    storage_class_id: uuid.UUID | None = None
    clear_storage_class: bool = False
    enabled: bool | None = None


def _get_cluster(db: Session, cluster_id: uuid.UUID) -> Cluster:
    c = db.get(Cluster, cluster_id)
    if c is None:
        raise NotFound("cluster")
    return c


def _validate_datastore_path(path: str) -> str:
    path = path.strip().rstrip("\\")
    if not WIN_PATH_RE.match(path + "\\"):
        raise AppError("invalid_path", "Path must be like C:\\ClusterStorage\\Volume1 or \\\\server\\share",
                       path=path)
    return path


@router.get("/clusters")
def list_clusters(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [cluster_to_dict(c) for c in db.scalars(select(Cluster).order_by(Cluster.name))]


@router.post("/clusters/discover")
async def discover(body: ConnectionIn, p: Principal = Depends(provider_admin)) -> dict:
    """Wizard step: connect to the given address, detect cluster or standalone host and enumerate nodes."""
    ep = Endpoint(address=body.address.strip(), username=body.username, password=body.password,
                  transport=body.winrm_transport, port=body.winrm_port, auth=body.winrm_auth,
                  cert_validation=body.winrm_cert_validation)
    try:
        return await run_in_threadpool(discovery_summary, get_driver(), ep)
    except HyperVError as exc:
        raise AppError("discovery_failed", str(exc), status_code=502, hv_code=exc.code) from exc


@router.post("/clusters")
def create_cluster(body: ClusterCreateIn, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(Cluster).where(func.lower(Cluster.name) == body.name.strip().lower())):
        raise Conflict("cluster_exists", "A cluster with this name already exists")
    if not FOLDER_RE.match(body.vm_folder):
        raise AppError("invalid_folder", "Invalid VM folder name")
    if body.kind == "standalone" and len(body.nodes) != 1:
        raise AppError("standalone_one_node", "A standalone host has exactly one node")
    cluster = Cluster(
        name=body.name.strip(), description=body.description, kind=body.kind, seed_address=body.seed_address.strip(),
        username=body.username, password_enc=encrypt_secret(body.password), winrm_transport=body.winrm_transport,
        winrm_port=body.winrm_port, winrm_auth=body.winrm_auth, winrm_cert_validation=body.winrm_cert_validation,
        console_username=body.console_username or None,
        console_password_enc=encrypt_secret(body.console_password) if body.console_password else None,
        console_port=body.console_port, vswitch_name=body.vswitch_name.strip(),
        ha_enabled=body.ha_enabled and body.kind == "cluster", vm_folder=body.vm_folder,
        cluster_name=body.cluster_name, cluster_fqdn=body.cluster_fqdn, cluster_address=body.cluster_address,
        domain=body.domain, status="unknown",
    )
    db.add(cluster)
    db.flush()
    seen = set()
    for n in body.nodes:
        if n.name.lower() in seen:
            continue
        seen.add(n.name.lower())
        db.add(Host(cluster_id=cluster.id, name=n.name.strip(), address=n.address.strip(), fqdn=n.fqdn,
                    state="Unknown", in_cluster=True))
    paths = set()
    for d in body.datastores:
        path = _validate_datastore_path(d.path)
        if path.lower() in paths:
            continue
        paths.add(path.lower())
        if d.storage_class_id and db.get(StorageClass, d.storage_class_id) is None:
            raise NotFound("storage_class")
        db.add(Datastore(cluster_id=cluster.id, name=d.name, path=path, kind=d.kind,
                         storage_class_id=d.storage_class_id, enabled=d.enabled, discovered=d.discovered,
                         capacity_gb=d.capacity_gb, free_gb=d.free_gb))
    db.flush()
    task = enqueue(db, "cluster.refresh", user=p.user, target_type="cluster", target_id=cluster.id,
                   target_name=cluster.name, lock_key=f"cluster:{cluster.id}")
    log_event(db, "cluster.created", user=p.user, target_type="cluster", target_id=cluster.id,
              target_name=cluster.name, ip=p.ip, details={"kind": cluster.kind, "nodes": len(seen)})
    db.commit()
    db.refresh(cluster)
    return {"cluster": cluster_to_dict(cluster, detail=True), "task": task_to_dict(task)}


@router.get("/clusters/{cluster_id}")
def get_cluster(cluster_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    c = _get_cluster(db, cluster_id)
    out = cluster_to_dict(c, detail=True)
    out["tenants"] = [{"id": str(t.id), "slug": t.slug, "name": t.name}
                      for t in db.scalars(select(Tenant).where(Tenant.cluster_id == c.id))]
    return out


@router.patch("/clusters/{cluster_id}")
def update_cluster(cluster_id: uuid.UUID, body: ClusterUpdateIn, p: Principal = Depends(provider_admin),
                   db: Session = Depends(get_db)) -> dict:
    c = _get_cluster(db, cluster_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        other = db.scalar(select(Cluster).where(func.lower(Cluster.name) == data["name"].strip().lower(),
                                                Cluster.id != c.id))
        if other:
            raise Conflict("cluster_exists", "A cluster with this name already exists")
        c.name = data["name"].strip()
    if data.get("vm_folder") and not FOLDER_RE.match(data["vm_folder"]):
        raise AppError("invalid_folder", "Invalid VM folder name")
    if data.get("vm_folder") and data["vm_folder"] != c.vm_folder:
        if db.scalar(select(func.count(VM.id)).where(VM.cluster_id == c.id)):
            raise Conflict("cluster_has_vms", "VM folder cannot be changed while VMs exist")
    for field in ("description", "username", "winrm_transport", "winrm_port", "winrm_auth", "winrm_cert_validation",
                  "console_port", "vswitch_name", "vm_folder", "seed_address"):
        if data.get(field) is not None:
            setattr(c, field, data[field])
    if data.get("password"):
        c.password_enc = encrypt_secret(data["password"])
    if "console_username" in data:
        c.console_username = data["console_username"] or None
        if not data["console_username"]:
            c.console_password_enc = None
    if data.get("console_password"):
        c.console_password_enc = encrypt_secret(data["console_password"])
    if data.get("ha_enabled") is not None:
        c.ha_enabled = bool(data["ha_enabled"]) and c.kind == "cluster"
    log_event(db, "cluster.updated", user=p.user, target_type="cluster", target_id=c.id, target_name=c.name, ip=p.ip,
              details={k: v for k, v in data.items() if "password" not in k})
    db.commit()
    return cluster_to_dict(c, detail=True)


@router.delete("/clusters/{cluster_id}")
def delete_cluster(cluster_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    c = _get_cluster(db, cluster_id)
    if db.scalar(select(func.count(Tenant.id)).where(Tenant.cluster_id == c.id)):
        raise Conflict("cluster_has_tenants", "Cluster is assigned to tenants")
    if db.scalar(select(func.count(VM.id)).where(VM.cluster_id == c.id)):
        raise Conflict("cluster_has_vms", "Cluster has managed VMs")
    log_event(db, "cluster.deleted", user=p.user, target_type="cluster", target_id=c.id, target_name=c.name, ip=p.ip)
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/clusters/{cluster_id}/refresh")
def refresh(cluster_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    c = _get_cluster(db, cluster_id)
    task = enqueue(db, "cluster.refresh", user=p.user, target_type="cluster", target_id=c.id, target_name=c.name,
                   lock_key=f"cluster:{c.id}")
    db.commit()
    return task_to_dict(task)


@router.get("/clusters/{cluster_id}/vms")
def cluster_vms(cluster_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list:
    _get_cluster(db, cluster_id)
    return [vm_to_dict(v) for v in db.scalars(select(VM).where(VM.cluster_id == cluster_id).order_by(VM.hv_name))]


@router.patch("/hosts/{host_id}")
def update_host(host_id: uuid.UUID, body: HostUpdateIn, p: Principal = Depends(provider_admin),
                db: Session = Depends(get_db)) -> dict:
    h = db.get(Host, host_id)
    if h is None:
        raise NotFound("host")
    if body.address:
        h.address = body.address.strip()
    if body.maintenance is not None:
        h.maintenance = body.maintenance
    log_event(db, "host.updated", user=p.user, target_type="host", target_id=h.id, target_name=h.name, ip=p.ip,
              details=body.model_dump(exclude_unset=True))
    db.commit()
    return host_to_dict(h)


@router.delete("/hosts/{host_id}")
def delete_host(host_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    h = db.get(Host, host_id)
    if h is None:
        raise NotFound("host")
    if h.in_cluster:
        raise Conflict("host_in_cluster", "Only hosts removed from the cluster can be deleted")
    log_event(db, "host.deleted", user=p.user, target_type="host", target_id=h.id, target_name=h.name, ip=p.ip)
    db.delete(h)
    db.commit()
    return {"ok": True}


@router.post("/clusters/{cluster_id}/datastores")
def add_datastore(cluster_id: uuid.UUID, body: DatastoreIn, p: Principal = Depends(provider_admin),
                  db: Session = Depends(get_db)) -> dict:
    c = _get_cluster(db, cluster_id)
    path = _validate_datastore_path(body.path)
    if any(d.path.lower() == path.lower() for d in c.datastores):
        raise Conflict("datastore_exists", "Datastore with this path already exists")
    if body.storage_class_id and db.get(StorageClass, body.storage_class_id) is None:
        raise NotFound("storage_class")
    d = Datastore(cluster_id=c.id, name=body.name, path=path, kind=body.kind, storage_class_id=body.storage_class_id,
                  enabled=body.enabled)
    db.add(d)
    log_event(db, "datastore.created", user=p.user, target_type="datastore", target_name=path, ip=p.ip)
    db.commit()
    db.refresh(d)
    return datastore_to_dict(d)


@router.patch("/datastores/{ds_id}")
def update_datastore(ds_id: uuid.UUID, body: DatastoreUpdateIn, p: Principal = Depends(provider_admin),
                     db: Session = Depends(get_db)) -> dict:
    d = db.get(Datastore, ds_id)
    if d is None:
        raise NotFound("datastore")
    used = db.scalar(select(func.count(VMDisk.id)).where(VMDisk.datastore_id == d.id)) or 0
    if body.name:
        d.name = body.name
    if body.path and body.path != d.path:
        if used:
            raise Conflict("datastore_in_use", "Datastore path cannot change while disks use it")
        d.path = _validate_datastore_path(body.path)
    if body.clear_storage_class:
        d.storage_class_id = None
    elif body.storage_class_id:
        if db.get(StorageClass, body.storage_class_id) is None:
            raise NotFound("storage_class")
        d.storage_class_id = body.storage_class_id
    if body.enabled is not None:
        d.enabled = body.enabled
    log_event(db, "datastore.updated", user=p.user, target_type="datastore", target_id=d.id, target_name=d.path,
              ip=p.ip)
    db.commit()
    db.refresh(d)
    return datastore_to_dict(d)


@router.delete("/datastores/{ds_id}")
def delete_datastore(ds_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    d = db.get(Datastore, ds_id)
    if d is None:
        raise NotFound("datastore")
    if db.scalar(select(func.count(VMDisk.id)).where(VMDisk.datastore_id == d.id)):
        raise Conflict("datastore_in_use", "Datastore holds VM disks")
    log_event(db, "datastore.deleted", user=p.user, target_type="datastore", target_name=d.path, ip=p.ip)
    db.delete(d)
    db.commit()
    return {"ok": True}


# ==========================================================================
# Storage classes
# ==========================================================================


class StorageClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    tier: int = Field(default=1, ge=1, le=100)


@router.get("/storage-classes")
def list_storage_classes(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for sc in db.scalars(select(StorageClass).order_by(StorageClass.tier, StorageClass.name)):
        d = storage_class_to_dict(sc)
        ds = db.scalars(select(Datastore).where(Datastore.storage_class_id == sc.id)).all()
        d["datastores"] = [datastore_to_dict(x) | {"cluster": x.cluster.name} for x in ds]
        d["capacity_gb"] = sum(x.capacity_gb or 0 for x in ds)
        d["free_gb"] = sum(x.free_gb or 0 for x in ds)
        d["allocated_gb"] = int(db.scalar(select(func.coalesce(func.sum(VMDisk.size_gb), 0))
                                          .where(VMDisk.storage_class_id == sc.id)) or 0)
        out.append(d)
    return out


@router.post("/storage-classes")
def create_storage_class(body: StorageClassIn, p: Principal = Depends(provider_admin),
                         db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(StorageClass).where(func.lower(StorageClass.name) == body.name.strip().lower())):
        raise Conflict("storage_class_exists", "Storage class already exists")
    sc = StorageClass(name=body.name.strip(), description=body.description, tier=body.tier)
    db.add(sc)
    log_event(db, "storage_class.created", user=p.user, target_type="storage_class", target_name=sc.name, ip=p.ip)
    db.commit()
    return storage_class_to_dict(sc)


@router.patch("/storage-classes/{sc_id}")
def update_storage_class(sc_id: uuid.UUID, body: StorageClassIn, p: Principal = Depends(provider_admin),
                         db: Session = Depends(get_db)) -> dict:
    sc = db.get(StorageClass, sc_id)
    if sc is None:
        raise NotFound("storage_class")
    other = db.scalar(select(StorageClass).where(func.lower(StorageClass.name) == body.name.strip().lower(),
                                                 StorageClass.id != sc.id))
    if other:
        raise Conflict("storage_class_exists", "Storage class already exists")
    sc.name, sc.description, sc.tier = body.name.strip(), body.description, body.tier
    db.commit()
    return storage_class_to_dict(sc)


@router.delete("/storage-classes/{sc_id}")
def delete_storage_class(sc_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    sc = db.get(StorageClass, sc_id)
    if sc is None:
        raise NotFound("storage_class")
    if db.scalar(select(func.count(VMDisk.id)).where(VMDisk.storage_class_id == sc.id)):
        raise Conflict("storage_class_in_use", "Storage class is used by VM disks")
    log_event(db, "storage_class.deleted", user=p.user, target_type="storage_class", target_name=sc.name, ip=p.ip)
    db.delete(sc)
    db.commit()
    return {"ok": True}


# ==========================================================================
# Tenants
# ==========================================================================


class StorageQuotaIn(BaseModel):
    storage_class_id: uuid.UUID
    limit_gb: int | None = Field(default=None, ge=0)


class TenantIn(BaseModel):
    slug: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    cluster_id: uuid.UUID
    enabled: bool = True
    quota_vcpu: int | None = Field(default=None, ge=0)
    quota_memory_mb: int | None = Field(default=None, ge=0)
    quota_vms: int | None = Field(default=None, ge=0)
    quota_iso_gb: int | None = Field(default=None, ge=0)
    quota_snapshots: int | None = Field(default=None, ge=0)
    vlans: str = ""
    storage_quotas: list[StorageQuotaIn] = []
    admin_username: str | None = None
    admin_password: str | None = None
    admin_full_name: str | None = None


class TenantUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    cluster_id: uuid.UUID | None = None
    enabled: bool | None = None
    quota_vcpu: int | None = Field(default=None, ge=0)
    quota_memory_mb: int | None = Field(default=None, ge=0)
    quota_vms: int | None = Field(default=None, ge=0)
    quota_iso_gb: int | None = Field(default=None, ge=0)
    quota_snapshots: int | None = Field(default=None, ge=0)
    vlans: str | None = None
    storage_quotas: list[StorageQuotaIn] | None = None


def _get_tenant(db: Session, tenant_id: uuid.UUID) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise NotFound("tenant")
    return t


def _apply_vlans(db: Session, tenant: Tenant, spec: str) -> None:
    try:
        wanted = set(parse_vlan_ranges(spec))
    except VlanParseError as exc:
        raise AppError("invalid_vlans", str(exc)) from exc
    if wanted:
        external = db.scalars(select(ExternalNetwork).where(ExternalNetwork.vlan_id.in_(wanted))).all()
        if external:
            raise Conflict("vlan_reserved_external", "VLANs are used by external networks",
                           vlans=", ".join(f"{n.vlan_id} ({n.name})" for n in external))
        taken = db.scalars(select(TenantVlan).where(TenantVlan.vlan_id.in_(wanted),
                                                    TenantVlan.tenant_id != tenant.id)).all()
        if taken:
            owners = {}
            for tv in taken:
                owners.setdefault(tv.tenant.slug, []).append(tv.vlan_id)
            raise Conflict("vlan_conflict", "VLANs already belong to other tenants",
                           vlans=", ".join(f"{k}: {sorted(v)[:10]}" for k, v in owners.items()))
    current = {v.vlan_id: v for v in tenant.vlans}
    removed = set(current) - wanted
    if removed:
        used = db.scalars(select(Network).where(Network.vlan_id.in_(removed))).all()
        if used:
            raise Conflict("vlan_in_use", "VLANs are used by networks",
                           vlans=", ".join(f"{n.vlan_id} ({n.name})" for n in used))
        for vid in removed:
            tenant.vlans.remove(current[vid])
    for vid in sorted(wanted - set(current)):
        tenant.vlans.append(TenantVlan(vlan_id=vid, tenant_id=tenant.id))


def _apply_storage_quotas(db: Session, tenant: Tenant, quotas: list[StorageQuotaIn]) -> None:
    wanted = {q.storage_class_id: q.limit_gb for q in quotas}
    for sc_id in wanted:
        if db.get(StorageClass, sc_id) is None:
            raise NotFound("storage_class")
    current = {q.storage_class_id: q for q in tenant.storage_quotas}
    for sc_id, q in current.items():
        if sc_id not in wanted:
            used = db.scalar(select(func.count(VMDisk.id)).join(VM, VM.id == VMDisk.vm_id)
                             .where(VM.tenant_id == tenant.id, VMDisk.storage_class_id == sc_id))
            if used:
                raise Conflict("storage_class_in_use", "Tenant has disks in this storage class",
                               storage_class=q.storage_class.name)
            tenant.storage_quotas.remove(q)
    for sc_id, limit in wanted.items():
        if sc_id in current:
            current[sc_id].limit_gb = limit
        else:
            tenant.storage_quotas.append(TenantStorageQuota(tenant_id=tenant.id, storage_class_id=sc_id,
                                                            limit_gb=limit))


def _tenant_extra(db: Session, t: Tenant) -> dict:
    return {
        "vm_count": db.scalar(select(func.count(VM.id)).where(VM.tenant_id == t.id)) or 0,
        "user_count": db.scalar(select(func.count(User.id)).where(User.tenant_id == t.id)) or 0,
        "network_count": db.scalar(select(func.count(Network.id)).where(Network.tenant_id == t.id)) or 0,
    }


@router.get("/tenants")
def list_tenants(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [tenant_to_dict(t, _tenant_extra(db, t)) for t in db.scalars(select(Tenant).order_by(Tenant.slug))]


@router.post("/tenants")
def create_tenant(body: TenantIn, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    slug = body.slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise AppError("invalid_slug", "Organization code: 2-32 chars, lowercase letters, digits and '-'")
    if db.scalar(select(Tenant).where(Tenant.slug == slug)):
        raise Conflict("tenant_exists", "Organization with this code already exists")
    if db.get(Cluster, body.cluster_id) is None:
        raise NotFound("cluster")
    t = Tenant(slug=slug, name=body.name.strip(), description=body.description, cluster_id=body.cluster_id,
               enabled=body.enabled, quota_vcpu=body.quota_vcpu, quota_memory_mb=body.quota_memory_mb,
               quota_vms=body.quota_vms, quota_iso_gb=body.quota_iso_gb, quota_snapshots=body.quota_snapshots)
    db.add(t)
    db.flush()
    _apply_vlans(db, t, body.vlans)
    _apply_storage_quotas(db, t, body.storage_quotas)
    if body.admin_username:
        admin = user_svc.create_user(db, t.id, {"username": body.admin_username, "password": body.admin_password or "",
                                                "full_name": body.admin_full_name or ""})
        user_svc.set_roles(db, admin, [{"role": "tenant_admin"}])
    log_event(db, "tenant.created", user=p.user, target_type="tenant", target_id=t.id, target_name=t.slug, ip=p.ip)
    db.commit()
    db.refresh(t)
    return tenant_to_dict(t, _tenant_extra(db, t))


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    t = _get_tenant(db, tenant_id)
    out = tenant_to_dict(t, _tenant_extra(db, t))
    out["usage"] = quota_report(db, t)
    out["networks"] = [{"id": str(n.id), "name": n.name, "vlan_id": n.vlan_id}
                       for n in db.scalars(select(Network).where(Network.tenant_id == t.id).order_by(Network.name))]
    out["vms"] = [vm_to_dict(v) for v in db.scalars(select(VM).where(VM.tenant_id == t.id).order_by(VM.name))]
    return out


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: uuid.UUID, body: TenantUpdateIn, p: Principal = Depends(provider_admin),
                  db: Session = Depends(get_db)) -> dict:
    t = _get_tenant(db, tenant_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("cluster_id") and data["cluster_id"] != t.cluster_id:
        if db.scalar(select(func.count(VM.id)).where(VM.tenant_id == t.id)):
            raise Conflict("tenant_has_vms", "Cluster cannot be changed while the tenant has VMs")
        if db.get(Cluster, data["cluster_id"]) is None:
            raise NotFound("cluster")
        t.cluster_id = data["cluster_id"]
    for field in ("name", "description", "enabled"):
        if data.get(field) is not None:
            setattr(t, field, data[field])
    for field in ("quota_vcpu", "quota_memory_mb", "quota_vms", "quota_iso_gb", "quota_snapshots"):
        if field in data:
            setattr(t, field, data[field])
    if data.get("vlans") is not None:
        _apply_vlans(db, t, data["vlans"])
    if body.storage_quotas is not None:
        _apply_storage_quotas(db, t, body.storage_quotas)
    log_event(db, "tenant.updated", user=p.user, target_type="tenant", target_id=t.id, target_name=t.slug, ip=p.ip,
              details={k: v for k, v in data.items() if k != "storage_quotas"} | (
                  {"storage_quotas": len(body.storage_quotas)} if body.storage_quotas is not None else {}))
    db.commit()
    db.refresh(t)
    return tenant_to_dict(t, _tenant_extra(db, t))


@router.delete("/tenants/{tenant_id}")
def delete_tenant(tenant_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    t = _get_tenant(db, tenant_id)
    if db.scalar(select(Router).where(Router.tenant_id == t.id)):
        raise Conflict("tenant_has_router", "Delete the organization router first")
    if db.scalar(select(func.count(VM.id)).where(VM.tenant_id == t.id)):
        raise Conflict("tenant_has_vms", "Delete all tenant VMs first")
    store = iso_svc.store(db)
    for iso in db.scalars(select(Iso).where(Iso.tenant_id == t.id)).all():
        store.delete(iso.file_name)
        db.delete(iso)
    db.query(Network).filter(Network.tenant_id == t.id).delete()
    log_event(db, "tenant.deleted", user=p.user, target_type="tenant", target_id=t.id, target_name=t.slug, ip=p.ip)
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---- tenant users (provider side) --------------------------------------------


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    full_name: str = ""
    email: str = ""
    is_active: bool = True
    roles: list[dict] = []


class UserUpdateIn(BaseModel):
    username: str | None = None
    password: str | None = None
    full_name: str | None = None
    email: str | None = None
    is_active: bool | None = None
    unlock: bool | None = None
    roles: list[dict] | None = None


@router.get("/tenants/{tenant_id}/users")
def tenant_users(tenant_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list:
    _get_tenant(db, tenant_id)
    return [user_to_dict(u) for u in db.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.username))]


@router.post("/tenants/{tenant_id}/users")
def create_tenant_user(tenant_id: uuid.UUID, body: UserIn, p: Principal = Depends(provider_admin),
                       db: Session = Depends(get_db)) -> dict:
    t = _get_tenant(db, tenant_id)
    u = user_svc.create_user(db, t.id, body.model_dump())
    user_svc.set_roles(db, u, body.roles or [{"role": "tenant_admin"}])
    log_event(db, "user.created", user=p.user, tenant_id=t.id, target_type="user", target_id=u.id,
              target_name=u.username, ip=p.ip)
    db.commit()
    db.refresh(u)
    return user_to_dict(u)


@router.patch("/tenants/{tenant_id}/users/{user_id}")
def update_tenant_user(tenant_id: uuid.UUID, user_id: uuid.UUID, body: UserUpdateIn,
                       p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None or u.tenant_id != tenant_id:
        raise NotFound("user")
    user_svc.update_user(db, u, body.model_dump(exclude_unset=True))
    if body.roles is not None:
        user_svc.set_roles(db, u, body.roles)
    log_event(db, "user.updated", user=p.user, tenant_id=tenant_id, target_type="user", target_id=u.id,
              target_name=u.username, ip=p.ip)
    db.commit()
    db.refresh(u)
    return user_to_dict(u)


@router.delete("/tenants/{tenant_id}/users/{user_id}")
def delete_tenant_user(tenant_id: uuid.UUID, user_id: uuid.UUID, p: Principal = Depends(provider_admin),
                       db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None or u.tenant_id != tenant_id:
        raise NotFound("user")
    log_event(db, "user.deleted", user=p.user, tenant_id=tenant_id, target_type="user", target_id=u.id,
              target_name=u.username, ip=p.ip)
    db.delete(u)
    db.commit()
    return {"ok": True}


# ==========================================================================
# Provider administrators
# ==========================================================================


@router.get("/users")
def provider_users(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list:
    return [user_to_dict(u) for u in db.scalars(select(User).where(User.tenant_id.is_(None)).order_by(User.username))]


@router.post("/users")
def create_provider_user(body: UserIn, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    u = user_svc.create_user(db, None, body.model_dump())
    log_event(db, "user.created", user=p.user, target_type="user", target_id=u.id, target_name=u.username, ip=p.ip)
    db.commit()
    db.refresh(u)
    return user_to_dict(u)


@router.patch("/users/{user_id}")
def update_provider_user(user_id: uuid.UUID, body: UserUpdateIn, p: Principal = Depends(provider_admin),
                         db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None or u.tenant_id is not None:
        raise NotFound("user")
    if body.is_active is False and u.id == p.user.id:
        raise AppError("cannot_disable_self", "You cannot disable your own account")
    user_svc.update_user(db, u, body.model_dump(exclude_unset=True, exclude={"roles"}))
    log_event(db, "user.updated", user=p.user, target_type="user", target_id=u.id, target_name=u.username, ip=p.ip)
    db.commit()
    db.refresh(u)
    return user_to_dict(u)


@router.delete("/users/{user_id}")
def delete_provider_user(user_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None or u.tenant_id is not None:
        raise NotFound("user")
    if u.id == p.user.id:
        raise AppError("cannot_delete_self", "You cannot delete your own account")
    log_event(db, "user.deleted", user=p.user, target_type="user", target_id=u.id, target_name=u.username, ip=p.ip)
    db.delete(u)
    db.commit()
    return {"ok": True}


# ==========================================================================
# Public ISO catalog (available to every tenant, not counted in quotas)
# ==========================================================================


class IsoInitIn(BaseModel):
    name: str = Field(min_length=5, max_length=255)
    size_bytes: int = Field(gt=0)
    description: str = ""


@router.get("/isos")
def list_isos(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list:
    rows = db.scalars(select(Iso).order_by(Iso.created_at.desc())).all()
    tenants = {t.id: t.slug for t in db.scalars(select(Tenant))}
    return [iso_to_dict(i) | {"tenant": tenants.get(i.tenant_id) if i.tenant_id else None} for i in rows]


@router.post("/isos")
def init_iso(body: IsoInitIn, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    iso = iso_svc.init_upload(db, None, p.user, body.name, body.size_bytes, body.description)
    log_event(db, "iso.upload_started", user=p.user, target_type="iso", target_id=iso.id, target_name=iso.name,
              ip=p.ip, details={"file": iso.file_name, "size": iso.size_bytes, "public": True})
    db.commit()
    return iso_to_dict(iso)


@router.put("/isos/{iso_id}/data")
async def upload_iso_chunk(iso_id: uuid.UUID, offset: int, request: Request, p: Principal = Depends(provider_admin),
                           db: Session = Depends(get_db)) -> dict:
    iso = db.get(Iso, iso_id)
    if iso is None or iso.tenant_id is not None:
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
def delete_iso(iso_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    iso = db.get(Iso, iso_id)
    if iso is None:
        raise NotFound("iso")
    iso_svc.delete_iso(db, iso)
    log_event(db, "iso.deleted", user=p.user, target_type="iso", target_id=iso_id, target_name=iso.name, ip=p.ip)
    db.commit()
    return {"ok": True}


# ==========================================================================
# VMs (read-only provider view) and settings
# ==========================================================================


@router.get("/vms")
def all_vms(cluster_id: uuid.UUID | None = None, tenant_id: uuid.UUID | None = None,
            p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list:
    q = select(VM).order_by(VM.hv_name)
    if cluster_id:
        q = q.where(VM.cluster_id == cluster_id)
    if tenant_id:
        q = q.where(VM.tenant_id == tenant_id)
    return [vm_to_dict(v) for v in db.scalars(q)]


@router.get("/settings")
def get_settings(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    return {key: public_setting(key, get_setting(db, key)) for key in DEFAULTS}


@router.put("/settings/{key}")
def put_setting(key: str, body: dict, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    if key not in DEFAULTS:
        raise NotFound("setting")
    allowed = set(DEFAULTS[key]) - {"password_enc"} - READONLY_FIELDS.get(key, set())
    value = {k: v for k, v in body.items() if k in allowed}
    if key == "iso_share":
        if "password" in body and body["password"]:
            value["password_enc"] = encrypt_secret(str(body["password"]))
        if body.get("clear_password"):
            value["password_enc"] = ""
        if value.get("unc") and not re.match(r"^\\\\[^\\]+\\[^\\]+", value["unc"]):
            raise AppError("invalid_path", "UNC path must look like \\\\server\\share", path=value["unc"])
    if key == "guacd" and "port" in value:
        value["port"] = int(value["port"])
    if key == "sync" and "interval_s" in value:
        value["interval_s"] = max(0, int(value["interval_s"]))
    if key == "metrics":
        if "interval_s" in value:
            iv = int(value["interval_s"])
            value["interval_s"] = 0 if iv <= 0 else min(3600, max(30, iv))
        if "retention_days" in value:
            value["retention_days"] = min(365, max(1, int(value["retention_days"])))
    if key == "router":
        _validate_router_setting(db, value)
    merged = set_setting(db, key, value)
    log_event(db, "settings.updated", user=p.user, target_type="settings", target_name=key, ip=p.ip,
              details={k: v for k, v in value.items() if "password" not in k})
    db.commit()
    return public_setting(key, merged)


def _validate_router_setting(db: Session, value: dict) -> None:
    from ..services import netutil

    if value.get("iso_id"):
        iso = db.get(Iso, uuid.UUID(str(value["iso_id"])))
        if iso is None or iso.tenant_id is not None or iso.status != "ready":
            raise AppError("router_iso_invalid", "Select a ready ISO from the public catalog")
    if value.get("mgmt_allowed"):
        netutil.normalize_addresses([x for x in re.split(r"[,;\s]+", str(value["mgmt_allowed"])) if x])
    for field, lo, hi in (("vcpu", 1, 64), ("memory_mb", 512, 65536), ("disk_gb", 4, 1024),
                          ("install_timeout_min", 10, 600)):
        if field in value:
            v = int(value[field])
            if not lo <= v <= hi:
                raise AppError("invalid_value", f"{field} must be between {lo} and {hi}", field=field)
            value[field] = v
    for field in ("mirror_host", "mirror_dir", "http_proxy", "timezone"):
        if any(c in str(value.get(field) or "") for c in " \t\n\"'\\$`;|&"):
            raise AppError("invalid_installer_value", f"Unsupported characters in {field}", field=field)


@router.post("/settings/iso_share/test")
async def test_iso_share(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    store = iso_svc.store(db)
    try:
        return await run_in_threadpool(store.test)
    except AppError as exc:
        return {"ok": False, "error": exc.message}


@router.post("/settings/guacd/test")
def test_guacd(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    cfg = get_setting(db, "guacd")
    try:
        with socket.create_connection((cfg["host"], int(cfg["port"])), timeout=5) as s:
            s.sendall(b"6.select,3.rdp;")
            s.settimeout(5)
            data = s.recv(4096).decode("utf-8", "replace")
        version = data.split(",")[1].split(".", 1)[1] if data.startswith("4.args") else ""
        return {"ok": data.startswith("4.args"), "version": version}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
