"""Tenant API: virtual router (Edge), firewall, NAT, IP sets, DHCP."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AppError, Conflict, NotFound
from ..models import (VM, Datastore, DhcpBinding, ExternalIp, FirewallRule, IpSet, NatRule, Network, NetworkDhcp,
                      Router, RouterNetwork, StorageClass, Task, TenantStorageQuota, VMDisk, VMNic, utcnow)
from ..security import encrypt_secret
from ..services import routers as rsvc
from ..services.audit import log_event
from ..services.quotas import Delta, check_quota, lock_tenant
from ..services.rbac import Principal
from ..services.routerconf import parse_leases
from ..services.routerprov import generate_ed25519, random_mac, random_password
from ..services.routerssh import get_transport
from ..services.settings import get_setting
from ..services.taskqueue import enqueue, task_to_dict
from ..utils import hv_vm_name
from .deps import require_tenant_perm, tenant_user
from .serializers import (dhcp_binding_to_dict, dhcp_network_to_dict, extip_to_dict, fwrule_to_dict, ipset_to_dict,
                          natrule_to_dict, router_to_dict)

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


# ---------------------------------------------------------------------------
# DHCP server (per connected network, served by the router on the SVI)
# ---------------------------------------------------------------------------


class DhcpIn(BaseModel):
    enabled: bool = True
    range_start: str
    range_end: str
    lease_time: int = 86400
    dns_servers: list[str] = []
    ntp_servers: list[str] = []
    domain_name: str = ""


class DhcpBindingIn(BaseModel):
    name: str
    mac: str
    address: str
    description: str = ""


def _router_network(db: Session, r: Router, network_id: uuid.UUID) -> Network:
    n = db.get(Network, network_id)
    if n is None or n.tenant_id != r.tenant_id:
        raise NotFound("network")
    if not any(rn.network_id == n.id for rn in r.networks):
        raise AppError("network_not_on_router", "Connect the network to the router first", network=n.name)
    return n


def _dhcp_served(r: Router, n: Network) -> bool:
    return n.dhcp is not None and n.dhcp.enabled and any(rn.network_id == n.id for rn in r.networks)


def _get_binding(db: Session, p: Principal, binding_id: uuid.UUID) -> DhcpBinding:
    b = db.get(DhcpBinding, binding_id)
    if b is None or b.network.tenant_id != p.tenant_id:
        raise NotFound("dhcp_binding")
    return b


def _mac_or_none(mac: str | None) -> str | None:
    try:
        return rsvc.normalize_mac(mac)
    except AppError:
        return None  # dynamic MAC not assigned by Hyper-V yet (000000000000)


@router.get("/router/dhcp")
def list_dhcp(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> list[dict]:
    """Connected networks with their DHCP settings, static bindings and the VM adapters attached to them."""
    require_tenant_perm(p, "router.view")
    r = _get_router(db, p)
    nets = sorted((rn.network for rn in r.networks), key=lambda n: n.vlan_id)
    nics: dict[uuid.UUID, list[dict[str, Any]]] = {}
    rows = db.execute(select(VMNic.network_id, VMNic.hv_name, VMNic.mac, VM.id, VM.name).join(VM, VM.id == VMNic.vm_id)
                      .where(VM.tenant_id == p.tenant_id, VM.kind == "vm",
                             VMNic.network_id.in_([n.id for n in nets] or [uuid.uuid4()]))
                      .order_by(VM.name, VMNic.position)).all()
    for network_id, nic, mac, vm_id, vm_name in rows:
        nics.setdefault(network_id, []).append({"vm_id": str(vm_id), "vm": vm_name, "nic": nic,
                                                "mac": _mac_or_none(mac)})
    return [dhcp_network_to_dict(n, rsvc.dhcp_range_suggestion(n), nics.get(n.id)) for n in nets]


@router.get("/router/dhcp/leases")
def dhcp_leases(p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    """Active leases read live from the router (systemd-networkd) over the management network."""
    require_tenant_perm(p, "router.view")
    r = _get_router(db, p)
    if r.status != "ready" or r.vm is None or not r.vm.hv_id:
        raise Conflict("router_not_ready", "The router is not deployed yet")
    nets = {rn.network.vlan_id: rn.network for rn in r.networks if _dhcp_served(r, rn.network)}
    now = utcnow()
    if not nets:
        return {"leases": [], "fetched_at": now.isoformat()}
    raw = get_transport().dhcp_leases(rsvc.router_conn(db, r), [f"vl{v}" for v in sorted(nets)])
    vm_by_mac: dict[str, tuple[str, str]] = {}
    rows = db.execute(select(VMNic.mac, VM.id, VM.name).join(VM, VM.id == VMNic.vm_id)
                      .where(VM.tenant_id == p.tenant_id, VM.kind == "vm", VMNic.mac.is_not(None))).all()
    for mac, vm_id, vm_name in rows:
        if (key := _mac_or_none(mac)) is not None:
            vm_by_mac[key] = (str(vm_id), vm_name)
    out = []
    for lease in parse_leases(raw):
        n = nets.get(lease["vlan"])
        if n is None:
            continue
        binding = next((b for b in n.dhcp_bindings if b.address == lease["address"]
                        and (lease["mac"] is None or b.mac == lease["mac"])), None)
        vm = vm_by_mac.get(lease["mac"] or "")
        expires_in = lease["expires_in"]
        out.append({
            "network_id": str(n.id), "network": n.name, "vlan_id": n.vlan_id, "address": lease["address"],
            "mac": lease["mac"], "client_id": lease["client_id"], "hostname": lease["hostname"],
            "expires_in": expires_in,
            "expires_at": (now + dt.timedelta(seconds=expires_in)).isoformat() if expires_in is not None else None,
            "binding": binding.name if binding else None, "vm_id": vm[0] if vm else None, "vm": vm[1] if vm else None,
        })
    out.sort(key=lambda x: (x["vlan_id"], tuple(int(o) for o in x["address"].split("."))))
    return {"leases": out, "fetched_at": now.isoformat()}


@router.put("/router/dhcp/{network_id}")
def put_dhcp(network_id: uuid.UUID, body: DhcpIn, p: Principal = Depends(tenant_user),
             db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    n = _router_network(db, r, network_id)
    was_served = _dhcp_served(r, n)
    data = rsvc.validate_dhcp(n, body.model_dump())
    if n.dhcp is None:
        n.dhcp = NetworkDhcp(network_id=n.id, **data)
    else:
        for k, v in data.items():
            setattr(n.dhcp, k, v)
    if was_served or data["enabled"]:
        rsvc.bump(db, r, p.user)
    log_event(db, "router.dhcp.update", user=p.user, target_type="network", target_id=n.id, target_name=n.name,
              ip=p.ip, details={"enabled": data["enabled"], "range": f"{data['range_start']}-{data['range_end']}"})
    db.commit()
    db.refresh(n)
    return dhcp_network_to_dict(n, rsvc.dhcp_range_suggestion(n))


@router.post("/router/dhcp/{network_id}/bindings")
def create_binding(network_id: uuid.UUID, body: DhcpBindingIn, p: Principal = Depends(tenant_user),
                   db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    n = _router_network(db, r, network_id)
    b = DhcpBinding(network_id=n.id, **rsvc.validate_dhcp_binding(n, body.model_dump()))
    db.add(b)
    db.flush()
    db.expire(n, ["dhcp_bindings"])
    if _dhcp_served(r, n):
        rsvc.bump(db, r, p.user)
    log_event(db, "router.dhcp.binding.create", user=p.user, target_type="network", target_id=n.id,
              target_name=n.name, ip=p.ip, details={"name": b.name, "mac": b.mac, "address": b.address})
    db.commit()
    return dhcp_binding_to_dict(b)


@router.patch("/router/dhcp/bindings/{binding_id}")
def update_binding(binding_id: uuid.UUID, body: DhcpBindingIn, p: Principal = Depends(tenant_user),
                   db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    b = _get_binding(db, p, binding_id)
    n = b.network
    for k, v in rsvc.validate_dhcp_binding(n, body.model_dump(), binding_id=b.id).items():
        setattr(b, k, v)
    if _dhcp_served(r, n):
        rsvc.bump(db, r, p.user)
    log_event(db, "router.dhcp.binding.update", user=p.user, target_type="network", target_id=n.id,
              target_name=n.name, ip=p.ip, details={"name": b.name, "mac": b.mac, "address": b.address})
    db.commit()
    return dhcp_binding_to_dict(b)


@router.delete("/router/dhcp/bindings/{binding_id}")
def delete_binding(binding_id: uuid.UUID, p: Principal = Depends(tenant_user), db: Session = Depends(get_db)) -> dict:
    r = _manage_router(db, p)
    b = _get_binding(db, p, binding_id)
    n = b.network
    log_event(db, "router.dhcp.binding.delete", user=p.user, target_type="network", target_id=n.id,
              target_name=n.name, ip=p.ip, details={"name": b.name, "mac": b.mac, "address": b.address})
    db.delete(b)
    db.flush()
    db.expire(n, ["dhcp_bindings"])
    if _dhcp_served(r, n):
        rsvc.bump(db, r, p.user)
    db.commit()
    return {"ok": True}


__all__ = ["router", "ExternalIp"]
