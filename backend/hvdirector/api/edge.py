"""Tenant API: virtual router (Edge), firewall, NAT, IP sets."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AppError, Conflict, NotFound
from ..models import (VM, Datastore, ExternalIp, FirewallRule, IpSet, NatRule, Network, Router, RouterNetwork,
                      StorageClass, Task, TenantStorageQuota, VMDisk)
from ..security import encrypt_secret
from ..services import routers as rsvc
from ..services.audit import log_event
from ..services.quotas import Delta, check_quota, lock_tenant
from ..services.rbac import Principal
from ..services.routerprov import generate_ed25519, random_mac, random_password
from ..services.settings import get_setting
from ..services.taskqueue import enqueue, task_to_dict
from ..utils import hv_vm_name
from .deps import require_tenant_perm, tenant_user
from .serializers import extip_to_dict, fwrule_to_dict, ipset_to_dict, natrule_to_dict, router_to_dict

router = APIRouter(prefix="/api/tenant", tags=["edge"])


def _get_router(db: Session, p: Principal, required: bool = True) -> Router | None:
    r = db.scalar(select(Router).where(Router.tenant_id == p.tenant_id))
    if r is None and required:
        raise NotFound("router")
    return r


def _wan_info(db: Session, tenant_id: uuid.UUID) -> dict[str, Any] | None:
    net, ips = rsvc.tenant_wan(db, tenant_id)
    if net is None:
        return None
    return {"network": net.name, "vlan_id": net.vlan_id, "cidr": net.cidr, "gateway": net.gateway,
            "ips": [{"id": str(i.id), "address": i.address} for i in ips]}


def _require_ready(r: Router) -> None:
    if r.status in ("deploying", "deleting"):
        raise Conflict("router_busy", "The router is being deployed or deleted")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class RouterCreateIn(BaseModel):
    name: str = Field(default="edge", min_length=1, max_length=64)
    storage_class_id: uuid.UUID
    network_ids: list[uuid.UUID] = []
    firewall_stateful: bool = True
    firewall_default_action: Literal["drop", "accept"] = "drop"


class RouterUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    network_ids: list[uuid.UUID] | None = None
    firewall_stateful: bool | None = None
    firewall_default_action: Literal["drop", "accept"] | None = None
    firewall_log_default: bool | None = None


@router.get("/router")
def get_router(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.view")
    r = _get_router(db, p, required=False)
    settings = get_setting(db, "router")
    mgmt = rsvc.tenant_mgmt_ip(db, p.tenant_id)
    nets = db.scalars(select(Network).where(Network.tenant_id == p.tenant_id).order_by(Network.vlan_id)).all()
    return {
        "router": router_to_dict(r, wan=_wan_info(db, p.tenant_id)) if r else None,
        "wan": _wan_info(db, p.tenant_id),
        "mgmt_ready": mgmt is not None,
        "iso_ready": bool(settings.get("iso_id")),
        "sizing": {"vcpu": settings["vcpu"], "memory_mb": settings["memory_mb"], "disk_gb": settings["disk_gb"]},
        "networks": [{"id": str(n.id), "name": n.name, "vlan_id": n.vlan_id, "cidr": n.cidr, "gateway": n.gateway,
                      "eligible": bool(n.cidr and n.gateway)} for n in nets],
    }


@router.post("/router")
def create_router(body: RouterCreateIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    tenant = lock_tenant(db, p.tenant_id)
    if _get_router(db, p, required=False) is not None:
        raise Conflict("router_exists", "The organization already has a router")
    mgmt = rsvc.tenant_mgmt_ip(db, tenant.id)
    if mgmt is None:
        raise AppError("router_no_mgmt_ip", "The provider has not allocated a management IP to the organization")
    settings = get_setting(db, "router")
    if not settings.get("iso_id"):
        raise AppError("router_iso_missing", "The provider has not selected a Debian 13 ISO for routers")
    quota = db.scalar(select(TenantStorageQuota).where(TenantStorageQuota.tenant_id == tenant.id,
                                                       TenantStorageQuota.storage_class_id == body.storage_class_id))
    if quota is None:
        sc = db.get(StorageClass, body.storage_class_id)
        raise AppError("storage_class_not_allowed", "Storage class is not available to the tenant",
                       storage_class=sc.name if sc else str(body.storage_class_id))
    cluster = tenant.cluster
    ha = cluster.kind == "cluster" and cluster.ha_enabled
    stores = db.scalars(select(Datastore).where(
        Datastore.cluster_id == cluster.id, Datastore.storage_class_id == body.storage_class_id,
        Datastore.enabled.is_(True), Datastore.present.is_(True))).all()
    stores = [d for d in stores if d.kind in ("csv", "smb")] if ha else list(stores)
    if not stores:
        sc = db.get(StorageClass, body.storage_class_id)
        raise AppError("no_datastore_for_class", "No datastore of this storage class on the tenant cluster",
                       storage_class=sc.name if sc else "")
    ds = max(stores, key=lambda d: d.free_gb or 0)
    nets = rsvc.validate_networks_for_router(db, tenant.id, body.network_ids)
    vcpu, mem, disk_gb = int(settings["vcpu"]), int(settings["memory_mb"]), int(settings["disk_gb"])
    check_quota(db, tenant, Delta(vcpu=vcpu, memory_mb=mem, vms=1, storage_gb={body.storage_class_id: disk_gb}))

    vm = VM(tenant_id=tenant.id, cluster_id=cluster.id, name=rsvc.ROUTER_VM_NAME, kind="router",
            description=f"Virtual router {body.name}", hv_name=hv_vm_name(tenant.slug, rsvc.ROUTER_VM_NAME),
            generation=2, secure_boot="linux", vcpu=vcpu, memory_mb=mem, status="creating", power_state="Unknown",
            created_by=p.user.id)
    db.add(vm)
    db.flush()
    db.add(VMDisk(vm_id=vm.id, datastore_id=ds.id, storage_class_id=body.storage_class_id, size_gb=disk_gb,
                  position=0))
    taken = set(db.scalars(select(Router.mgmt_mac)).all()) | set(db.scalars(select(Router.wan_mac)).all()) | \
        set(db.scalars(select(Router.lan_mac)).all())
    macs = []
    for _ in range(3):
        mac = random_mac(taken)
        taken.add(mac)
        macs.append(mac)
    host_priv, host_pub = generate_ed25519(f"{tenant.slug}-edge")
    r = Router(tenant_id=tenant.id, vm_id=vm.id, name=body.name.strip(), status="deploying", mgmt_ip_id=mgmt.id,
               storage_class_id=body.storage_class_id, mgmt_mac=macs[0], wan_mac=macs[1], lan_mac=macs[2],
               firewall_stateful=body.firewall_stateful, firewall_default_action=body.firewall_default_action,
               root_password_enc=encrypt_secret(random_password()), host_key_enc=encrypt_secret(host_priv),
               host_key_pub=host_pub, created_by=p.user.id)
    db.add(r)
    db.flush()
    for n in nets:
        db.add(RouterNetwork(router_id=r.id, network_id=n.id))
    task = enqueue(db, "router.deploy", user=p.user, tenant_id=tenant.id, target_type="router", target_id=r.id,
                   target_name=r.name, lock_key=f"router:{r.id}")
    log_event(db, "router.create", user=p.user, target_type="router", target_id=r.id, target_name=r.name, ip=p.ip,
              details={"networks": [n.name for n in nets], "stateful": body.firewall_stateful})
    db.commit()
    db.refresh(r)
    return {"router": router_to_dict(r, wan=_wan_info(db, tenant.id)), "task": task_to_dict(task)}


@router.patch("/router")
def update_router(body: RouterUpdateIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    r = _get_router(db, p)
    _require_ready(r)
    if body.name:
        r.name = body.name.strip()
    if body.network_ids is not None:
        nets = rsvc.validate_networks_for_router(db, p.tenant_id, body.network_ids)
        wanted = {n.id for n in nets}
        for rn in list(r.networks):
            if rn.network_id not in wanted:
                r.networks.remove(rn)
        have = {rn.network_id for rn in r.networks}
        for n in nets:
            if n.id not in have:
                r.networks.append(RouterNetwork(router_id=r.id, network_id=n.id))
    for field in ("firewall_stateful", "firewall_default_action", "firewall_log_default"):
        v = getattr(body, field)
        if v is not None:
            setattr(r, field, v)
    task = rsvc.bump(db, r, p.user)
    log_event(db, "router.update", user=p.user, target_type="router", target_id=r.id, target_name=r.name, ip=p.ip,
              details=body.model_dump(mode="json", exclude_none=True))
    db.commit()
    db.refresh(r)
    return {"router": router_to_dict(r, wan=_wan_info(db, p.tenant_id)), "task": task_to_dict(task) if task else None}


@router.post("/router/apply")
def reapply(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    r = _get_router(db, p)
    _require_ready(r)
    task = enqueue(db, "router.apply", user=p.user, tenant_id=r.tenant_id, target_type="router", target_id=r.id,
                   target_name=r.name, params={"force": True}, lock_key=f"router:{r.id}")
    db.commit()
    return task_to_dict(task)


@router.delete("/router")
def delete_router(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    r = _get_router(db, p)
    running = db.scalar(select(Task).where(Task.target_id == r.id, Task.status == "running"))
    if running is not None or r.status == "deleting":
        raise Conflict("router_busy", "The router is busy, try again later")
    r.status = "deleting"
    task = enqueue(db, "router.delete", user=p.user, tenant_id=r.tenant_id, target_type="router", target_id=r.id,
                   target_name=r.name, lock_key=f"router:{r.id}")
    log_event(db, "router.delete", user=p.user, target_type="router", target_id=r.id, target_name=r.name, ip=p.ip)
    db.commit()
    return task_to_dict(task)


@router.get("/external-ips")
def external_ips(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "router.view")
    _, ips = rsvc.tenant_wan(db, p.tenant_id)
    r = _get_router(db, p, required=False)
    used: dict[uuid.UUID, list[str]] = {}
    if r is not None:
        for rule in r.nat_rules:
            used.setdefault(rule.external_ip_id, []).append(rule.name)
    return [extip_to_dict(i, used_by=used.get(i.id)) for i in ips]


# ---------------------------------------------------------------------------
# Firewall rules
# ---------------------------------------------------------------------------


class AddressItem(BaseModel):
    type: Literal["cidr", "ipset", "network"]
    value: str


class FwRuleIn(BaseModel):
    name: str
    enabled: bool = True
    action: Literal["allow", "drop", "reject"] = "allow"
    protocol: Literal["any", "tcp", "udp", "tcp_udp", "icmp"] = "any"
    sources: list[AddressItem] = []
    destinations: list[AddressItem] = []
    src_ports: str = ""
    dst_ports: str = ""
    log: bool = False
    description: str = ""


class OrderIn(BaseModel):
    ids: list[uuid.UUID]


def _manage_router(db: Session, p: Principal) -> Router:
    require_tenant_perm(p, "router.manage")
    r = _get_router(db, p)
    if r.status == "deleting":
        raise Conflict("router_busy", "The router is being deleted")
    return r


def _get_fw(db: Session, r: Router, rule_id: uuid.UUID) -> FirewallRule:
    rule = db.get(FirewallRule, rule_id)
    if rule is None or rule.router_id != r.id:
        raise NotFound("firewall_rule")
    return rule


def _reorder(items: list, ids: list[uuid.UUID]) -> None:  # noqa: ANN001
    by_id = {i.id: i for i in items}
    if set(ids) != set(by_id) or len(ids) != len(by_id):
        raise AppError("invalid_order", "The order must list every rule exactly once")
    for pos, rid in enumerate(ids):
        by_id[rid].position = pos


@router.get("/router/firewall")
def list_fw(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "router.view")
    return [fwrule_to_dict(x) for x in _get_router(db, p).firewall_rules]


@router.post("/router/firewall")
def create_fw(body: FwRuleIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    data = rsvc.validate_fw_rule(db, p.tenant_id, body.model_dump())
    pos = max([x.position for x in r.firewall_rules] + [-1]) + 1
    rule = FirewallRule(router_id=r.id, position=pos, **data)
    db.add(rule)
    db.flush()
    db.expire(r, ["firewall_rules"])
    rsvc.bump(db, r, p.user)
    log_event(db, "router.firewall.create", user=p.user, target_type="router", target_id=r.id, target_name=rule.name,
              ip=p.ip)
    db.commit()
    return fwrule_to_dict(rule)


@router.patch("/router/firewall/{rule_id}")
def update_fw(rule_id: uuid.UUID, body: FwRuleIn, p: Principal = Depends(tenant_user),
              db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    rule = _get_fw(db, r, rule_id)
    for k, v in rsvc.validate_fw_rule(db, p.tenant_id, body.model_dump()).items():
        setattr(rule, k, v)
    rsvc.bump(db, r, p.user)
    log_event(db, "router.firewall.update", user=p.user, target_type="router", target_id=r.id, target_name=rule.name,
              ip=p.ip)
    db.commit()
    return fwrule_to_dict(rule)


@router.delete("/router/firewall/{rule_id}")
def delete_fw(rule_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    rule = _get_fw(db, r, rule_id)
    log_event(db, "router.firewall.delete", user=p.user, target_type="router", target_id=r.id, target_name=rule.name,
              ip=p.ip)
    db.delete(rule)
    db.flush()
    db.expire(r, ["firewall_rules"])
    rsvc.bump(db, r, p.user)
    db.commit()
    return {"ok": True}


@router.post("/router/firewall/order")
def order_fw(body: OrderIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    r = _manage_router(db, p)
    _reorder(r.firewall_rules, body.ids)
    rsvc.bump(db, r, p.user)
    db.commit()
    db.expire(r, ["firewall_rules"])
    return [fwrule_to_dict(x) for x in r.firewall_rules]


# ---------------------------------------------------------------------------
# NAT rules
# ---------------------------------------------------------------------------


class NatRuleIn(BaseModel):
    name: str
    enabled: bool = True
    kind: Literal["snat", "dnat"]
    external_ip_id: uuid.UUID
    internal_address: str
    protocol: Literal["any", "tcp", "udp", "tcp_udp"] = "any"
    external_port: str = ""
    internal_port: str = ""
    match_address: str = ""
    log: bool = False
    description: str = ""


def _get_nat(db: Session, r: Router, rule_id: uuid.UUID) -> NatRule:
    rule = db.get(NatRule, rule_id)
    if rule is None or rule.router_id != r.id:
        raise NotFound("nat_rule")
    return rule


@router.get("/router/nat")
def list_nat(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "router.view")
    return [natrule_to_dict(x) for x in _get_router(db, p).nat_rules]


@router.post("/router/nat")
def create_nat(body: NatRuleIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    data = rsvc.validate_nat_rule(db, r, body.model_dump())
    pos = max([x.position for x in r.nat_rules] + [-1]) + 1
    rule = NatRule(router_id=r.id, position=pos, **data)
    db.add(rule)
    db.flush()
    db.expire(r, ["nat_rules"])
    rsvc.bump(db, r, p.user)
    log_event(db, "router.nat.create", user=p.user, target_type="router", target_id=r.id, target_name=rule.name,
              ip=p.ip, details={"kind": rule.kind})
    db.commit()
    db.refresh(rule)
    return natrule_to_dict(rule)


@router.patch("/router/nat/{rule_id}")
def update_nat(rule_id: uuid.UUID, body: NatRuleIn, p: Principal = Depends(tenant_user),
               db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    rule = _get_nat(db, r, rule_id)
    for k, v in rsvc.validate_nat_rule(db, r, body.model_dump()).items():
        setattr(rule, k, v)
    rsvc.bump(db, r, p.user)
    log_event(db, "router.nat.update", user=p.user, target_type="router", target_id=r.id, target_name=rule.name,
              ip=p.ip)
    db.commit()
    db.refresh(rule)
    return natrule_to_dict(rule)


@router.delete("/router/nat/{rule_id}")
def delete_nat(rule_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    rule = _get_nat(db, r, rule_id)
    log_event(db, "router.nat.delete", user=p.user, target_type="router", target_id=r.id, target_name=rule.name,
              ip=p.ip)
    db.delete(rule)
    db.flush()
    db.expire(r, ["nat_rules"])
    rsvc.bump(db, r, p.user)
    db.commit()
    return {"ok": True}


@router.post("/router/nat/order")
def order_nat(body: OrderIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    r = _manage_router(db, p)
    _reorder(r.nat_rules, body.ids)
    rsvc.bump(db, r, p.user)
    db.commit()
    db.expire(r, ["nat_rules"])
    return [natrule_to_dict(x) for x in r.nat_rules]


# ---------------------------------------------------------------------------
# IP sets
# ---------------------------------------------------------------------------


class IpSetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    entries: list[str] = []


def _get_ipset(db: Session, p: Principal, set_id: uuid.UUID) -> IpSet:
    s = db.get(IpSet, set_id)
    if s is None or s.tenant_id != p.tenant_id:
        raise NotFound("ip_set")
    return s


@router.get("/ip-sets")
def list_ipsets(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    require_tenant_perm(p, "router.view")
    rows = db.scalars(select(IpSet).where(IpSet.tenant_id == p.tenant_id).order_by(IpSet.name)).all()
    return [ipset_to_dict(s, rsvc.rules_referencing(db, p.tenant_id, "ipset", str(s.id))) for s in rows]


@router.post("/ip-sets")
def create_ipset(body: IpSetIn, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    name = body.name.strip()
    if db.scalar(select(IpSet).where(IpSet.tenant_id == p.tenant_id, func.lower(IpSet.name) == name.lower())):
        raise Conflict("ipset_exists", "An IP set with this name already exists")
    s = IpSet(tenant_id=p.tenant_id, name=name, description=body.description,
              entries=rsvc.validate_ipset_entries(body.entries))
    db.add(s)
    log_event(db, "router.ipset.create", user=p.user, target_type="ip_set", target_name=name, ip=p.ip)
    db.commit()
    return ipset_to_dict(s)


@router.patch("/ip-sets/{set_id}")
def update_ipset(set_id: uuid.UUID, body: IpSetIn, p: Principal = Depends(tenant_user),
                 db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    s = _get_ipset(db, p, set_id)
    name = body.name.strip()
    if db.scalar(select(IpSet).where(IpSet.tenant_id == p.tenant_id, func.lower(IpSet.name) == name.lower(),
                                     IpSet.id != s.id)):
        raise Conflict("ipset_exists", "An IP set with this name already exists")
    s.name, s.description, s.entries = name, body.description, rsvc.validate_ipset_entries(body.entries)
    used = rsvc.rules_referencing(db, p.tenant_id, "ipset", str(s.id))
    if used:
        rsvc.bump_tenant(db, p.tenant_id, p.user)
    log_event(db, "router.ipset.update", user=p.user, target_type="ip_set", target_name=name, ip=p.ip)
    db.commit()
    return ipset_to_dict(s, used)


@router.delete("/ip-sets/{set_id}")
def delete_ipset(set_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    require_tenant_perm(p, "router.manage")
    s = _get_ipset(db, p, set_id)
    used = rsvc.rules_referencing(db, p.tenant_id, "ipset", str(s.id))
    if used:
        raise Conflict("ipset_in_use", "The IP set is used by firewall rules", rules=", ".join(used))
    log_event(db, "router.ipset.delete", user=p.user, target_type="ip_set", target_name=s.name, ip=p.ip)
    db.delete(s)
    db.commit()
    return {"ok": True}


__all__ = ["router", "ExternalIp"]
