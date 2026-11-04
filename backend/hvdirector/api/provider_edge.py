"""Provider API: external networks (WAN / management), IP allocations to tenants, virtual routers."""

from __future__ import annotations

import datetime as dt
import ipaddress
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import AppError, Conflict, NotFound
from ..models import VM, ConsoleTicket, ExternalIp, ExternalNetwork, NatRule, Router, Tenant, TenantVlan, utcnow
from ..security import decrypt_secret, hash_token, new_token
from ..services import netutil
from ..services import routers as rsvc
from ..services.audit import log_event
from ..services.rbac import Principal
from ..services.settings import get_setting, public_setting
from ..services.taskqueue import enqueue, task_to_dict
from .deps import provider_admin
from .serializers import extip_to_dict, extnet_to_dict, router_to_dict

router = APIRouter(prefix="/api/provider", tags=["provider-edge"])

MAX_ALLOCATION = 256


# ---------------------------------------------------------------------------
# External networks
# ---------------------------------------------------------------------------


class ExtNetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    purpose: Literal["wan", "mgmt"]
    vlan_id: int = Field(ge=1, le=4094)
    cidr: str
    gateway: str | None = None
    dns: str = ""
    vswitch_name: str = ""
    description: str = ""


class ExtNetUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    gateway: str | None = None
    dns: str | None = None
    vswitch_name: str | None = None
    description: str | None = None


def _validate_extnet(db: Session, body: ExtNetIn | ExtNetUpdateIn, cidr: str, exclude: uuid.UUID | None) -> None:
    net = netutil.parse_cidr(cidr, "cidr")
    if body.gateway:
        netutil.parse_interface(body.gateway, str(net))
    for d in re.split(r"[,;\s]+", body.dns or ""):
        if d:
            netutil.parse_ip(d, "dns")
    if body.name:
        q = select(ExternalNetwork).where(func.lower(ExternalNetwork.name) == body.name.strip().lower())
        if exclude:
            q = q.where(ExternalNetwork.id != exclude)
        if db.scalar(q):
            raise Conflict("extnet_exists", "An external network with this name already exists")


def _allocated(db: Session, net_id: uuid.UUID) -> int:
    return db.scalar(select(func.count(ExternalIp.id)).where(ExternalIp.network_id == net_id)) or 0


def _routers_on_network(db: Session, net: ExternalNetwork) -> list[Router]:
    tenant_ids = set(db.scalars(select(ExternalIp.tenant_id).where(ExternalIp.network_id == net.id)).all())
    return db.scalars(select(Router).where(Router.tenant_id.in_(tenant_ids or {uuid.uuid4()}))).all()


@router.get("/external-networks")
def list_extnets(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ExternalNetwork).order_by(ExternalNetwork.purpose, ExternalNetwork.name)).all()
    return [extnet_to_dict(n, _allocated(db, n.id)) for n in rows]


@router.post("/external-networks")
def create_extnet(body: ExtNetIn, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    _validate_extnet(db, body, body.cidr, None)
    owner = db.scalar(select(TenantVlan).where(TenantVlan.vlan_id == body.vlan_id))
    if owner is not None:
        raise Conflict("vlan_in_tenant_pool", "The VLAN belongs to an organization VLAN pool",
                       vlan=body.vlan_id, tenant=owner.tenant.slug)
    n = ExternalNetwork(name=body.name.strip(), purpose=body.purpose, vlan_id=body.vlan_id,
                        cidr=str(netutil.parse_cidr(body.cidr)), gateway=body.gateway or None, dns=body.dns.strip(),
                        vswitch_name=body.vswitch_name.strip(), description=body.description)
    db.add(n)
    log_event(db, "extnet.created", user=p.user, target_type="external_network", target_name=n.name, ip=p.ip,
              details={"purpose": n.purpose, "vlan": n.vlan_id, "cidr": n.cidr})
    db.commit()
    return extnet_to_dict(n)


@router.patch("/external-networks/{net_id}")
def update_extnet(net_id: uuid.UUID, body: ExtNetUpdateIn, p: Principal = Depends(provider_admin),
                  db: Session = Depends(get_db)) -> dict:
    n = db.get(ExternalNetwork, net_id)
    if n is None:
        raise NotFound("external_network")
    _validate_extnet(db, body, n.cidr, n.id)
    data = body.model_dump(exclude_unset=True)
    if "gateway" in data and data["gateway"]:
        gw = ipaddress.IPv4Address(data["gateway"])
        if db.scalar(select(ExternalIp).where(ExternalIp.network_id == n.id, ExternalIp.address == str(gw))):
            raise Conflict("ip_allocated", "The gateway address is allocated to an organization", address=str(gw))
    for field in ("name", "gateway", "dns", "vswitch_name", "description"):
        if field in data and data[field] is not None:
            setattr(n, field, data[field].strip() if isinstance(data[field], str) else data[field])
    if "gateway" in data and not data["gateway"]:
        n.gateway = None
    for r in _routers_on_network(db, n):
        rsvc.bump(db, r, p.user)
    log_event(db, "extnet.updated", user=p.user, target_type="external_network", target_name=n.name, ip=p.ip)
    db.commit()
    return extnet_to_dict(n, _allocated(db, n.id))


@router.delete("/external-networks/{net_id}")
def delete_extnet(net_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    n = db.get(ExternalNetwork, net_id)
    if n is None:
        raise NotFound("external_network")
    if _allocated(db, n.id):
        raise Conflict("extnet_in_use", "Addresses of this network are allocated to organizations")
    log_event(db, "extnet.deleted", user=p.user, target_type="external_network", target_name=n.name, ip=p.ip)
    db.delete(n)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Allocations of external addresses to tenants
# ---------------------------------------------------------------------------


class AllocateIn(BaseModel):
    network_id: uuid.UUID
    addresses: str = ""  # "203.0.113.10, 203.0.113.20-203.0.113.25"
    count: int | None = Field(default=None, ge=1, le=MAX_ALLOCATION)
    description: str = ""


def _get_tenant(db: Session, tenant_id: uuid.UUID) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise NotFound("tenant")
    return t


@router.get("/tenants/{tenant_id}/external-ips")
def tenant_external_ips(tenant_id: uuid.UUID, p: Principal = Depends(provider_admin),
                        db: Session = Depends(get_db)) -> dict:
    t = _get_tenant(db, tenant_id)
    ips = db.scalars(select(ExternalIp).where(ExternalIp.tenant_id == t.id)).all()
    nat_use: dict[uuid.UUID, list[str]] = {}
    for rule in db.scalars(select(NatRule).join(Router, Router.id == NatRule.router_id).where(Router.tenant_id == t.id)):
        nat_use.setdefault(rule.external_ip_id, []).append(rule.name)
    r = db.scalar(select(Router).where(Router.tenant_id == t.id))
    items = sorted(ips, key=lambda i: (i.network.purpose, ipaddress.IPv4Address(i.address)))
    return {
        "items": [extip_to_dict(i, t.slug, nat_use.get(i.id) or (["router"] if r and r.mgmt_ip_id == i.id else []))
                  for i in items],
        "router": router_to_dict(r, provider=True) if r else None,
    }


def _free_hosts(db: Session, net: ExternalNetwork) -> list[ipaddress.IPv4Address]:
    cidr = ipaddress.IPv4Network(net.cidr)
    used = set(db.scalars(select(ExternalIp.address).where(ExternalIp.network_id == net.id)).all())
    if net.gateway:
        used.add(net.gateway)
    return [ip for ip in cidr.hosts() if str(ip) not in used]


@router.post("/tenants/{tenant_id}/external-ips")
def allocate(tenant_id: uuid.UUID, body: AllocateIn, p: Principal = Depends(provider_admin),
             db: Session = Depends(get_db)) -> dict:
    t = _get_tenant(db, tenant_id)
    net = db.get(ExternalNetwork, body.network_id)
    if net is None:
        raise NotFound("external_network")
    db.execute(select(ExternalNetwork).where(ExternalNetwork.id == net.id).with_for_update())
    cidr = ipaddress.IPv4Network(net.cidr)
    wanted: list[ipaddress.IPv4Address] = []
    if body.count:
        free = _free_hosts(db, net)
        if len(free) < body.count:
            raise Conflict("not_enough_addresses", "Not enough free addresses", free=len(free))
        wanted = free[:body.count]
    else:
        for item in re.split(r"[,;\s]+", body.addresses.strip()):
            if not item:
                continue
            for n in netutil.parse_address_item(item):
                wanted.extend([n.network_address] if n.prefixlen == 32 else list(n))
        if not wanted:
            raise AppError("no_addresses", "Specify addresses or a count")
    if len(wanted) > MAX_ALLOCATION:
        raise AppError("too_many_addresses", "Too many addresses in one request", max=MAX_ALLOCATION)
    existing = db.scalars(select(ExternalIp).join(ExternalNetwork, ExternalNetwork.id == ExternalIp.network_id)
                          .where(ExternalIp.tenant_id == t.id, ExternalNetwork.purpose == net.purpose)).all()
    if net.purpose == "mgmt":
        if existing or len(wanted) != 1:
            raise Conflict("mgmt_ip_single", "An organization has exactly one management IP (one router)")
    elif existing and any(e.network_id != net.id for e in existing):
        raise Conflict("wan_single_network", "All WAN addresses of an organization must be in one external network",
                       network=existing[0].network.name)
    created = []
    for ip in wanted:
        if ip not in cidr or (cidr.prefixlen < 31 and ip in (cidr.network_address, cidr.broadcast_address)):
            raise AppError("ip_not_in_network", f"{ip} is not a host address of {cidr}", value=str(ip),
                           network=str(cidr))
        if net.gateway and str(ip) == net.gateway:
            raise Conflict("ip_is_gateway", "The address is the gateway of the network", address=str(ip))
        other = db.scalar(select(ExternalIp).where(ExternalIp.network_id == net.id, ExternalIp.address == str(ip)))
        if other is not None:
            raise Conflict("ip_allocated", "The address is already allocated", address=str(ip),
                           tenant=db.get(Tenant, other.tenant_id).slug)
        e = ExternalIp(network_id=net.id, tenant_id=t.id, address=str(ip), description=body.description)
        db.add(e)
        db.flush()
        created.append(e)
    if net.purpose == "wan":
        rsvc.bump_tenant(db, t.id, p.user)
    log_event(db, "extip.allocated", user=p.user, tenant_id=t.id, target_type="tenant", target_id=t.id,
              target_name=t.slug, ip=p.ip, details={"network": net.name, "addresses": [e.address for e in created]})
    db.commit()
    return {"items": [extip_to_dict(e, t.slug) for e in created]}


@router.delete("/external-ips/{ip_id}")
def release(ip_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    e = db.get(ExternalIp, ip_id)
    if e is None:
        raise NotFound("external_ip")
    r = db.scalar(select(Router).where(Router.tenant_id == e.tenant_id))
    if r is not None and r.mgmt_ip_id == e.id:
        raise Conflict("ip_in_use_router", "The management IP is used by the organization router")
    rules = db.scalars(select(NatRule.name).where(NatRule.external_ip_id == e.id)).all()
    if rules:
        raise Conflict("ip_in_use_nat", "The address is used by NAT rules", rules=", ".join(rules))
    purpose, tenant_id, address = e.network.purpose, e.tenant_id, e.address
    db.delete(e)
    db.flush()
    if purpose == "wan":
        rsvc.bump_tenant(db, tenant_id, p.user)
    log_event(db, "extip.released", user=p.user, tenant_id=tenant_id, target_type="external_ip", target_name=address,
              ip=p.ip)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Routers (provider view)
# ---------------------------------------------------------------------------


def _wan(db: Session, tenant_id: uuid.UUID) -> dict | None:
    net, ips = rsvc.tenant_wan(db, tenant_id)
    if net is None:
        return None
    return {"network": net.name, "vlan_id": net.vlan_id, "cidr": net.cidr, "gateway": net.gateway,
            "ips": [{"id": str(i.id), "address": i.address} for i in ips]}


def _get_router(db: Session, router_id: uuid.UUID) -> Router:
    r = db.get(Router, router_id)
    if r is None:
        raise NotFound("router")
    return r


@router.get("/routers")
def list_routers(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Router).order_by(Router.created_at)).all()
    return [router_to_dict(r, provider=True, wan=_wan(db, r.tenant_id)) for r in rows]


@router.post("/routers/{router_id}/apply")
def apply_router(router_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    r = _get_router(db, router_id)
    if r.status in ("deploying", "deleting"):
        raise Conflict("router_busy", "The router is being deployed or deleted")
    task = enqueue(db, "router.apply", user=p.user, tenant_id=r.tenant_id, target_type="router", target_id=r.id,
                   target_name=r.name, params={"force": True}, lock_key=f"router:{r.id}")
    db.commit()
    return task_to_dict(task)


@router.delete("/routers/{router_id}")
def delete_router(router_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    r = _get_router(db, router_id)
    if r.status == "deleting":
        raise Conflict("router_busy", "The router is already being deleted")
    r.status = "deleting"
    task = enqueue(db, "router.delete", user=p.user, tenant_id=r.tenant_id, target_type="router", target_id=r.id,
                   target_name=r.name, lock_key=f"router:{r.id}")
    log_event(db, "router.delete", user=p.user, tenant_id=r.tenant_id, target_type="router", target_id=r.id,
              target_name=r.name, ip=p.ip)
    db.commit()
    return task_to_dict(task)


@router.get("/routers/{router_id}/secret")
def router_secret(router_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    """Root password of the router OS (console break-glass access); every read is audited."""
    r = _get_router(db, router_id)
    log_event(db, "router.secret_viewed", user=p.user, tenant_id=r.tenant_id, target_type="router", target_id=r.id,
              target_name=r.name, ip=p.ip)
    db.commit()
    return {"root_password": decrypt_secret(r.root_password_enc), "host_key": r.host_key_pub}


@router.get("/router-settings/ssh-key")
def ssh_key(p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    _, public = rsvc.ensure_keypair(db)
    db.commit()
    return {"public_key": public, "settings": public_setting("router", get_setting(db, "router"))}


@router.post("/vms/{vm_id}/console")
def provider_console(vm_id: uuid.UUID, p: Principal = Depends(provider_admin), db: Session = Depends(get_db)) -> dict:
    """Console of any VM (including router VMs) for provider administrators."""
    vm = db.get(VM, vm_id)
    if vm is None:
        raise NotFound("vm")
    if not vm.hv_id or vm.power_state not in ("Running", "Paused"):
        raise Conflict("vm_not_running", "Console is available only for a running VM")
    ttl = int(get_setting(db, "console").get("ticket_ttl_s", 60))
    ticket = new_token(24)
    db.add(ConsoleTicket(token_hash=hash_token(ticket), user_id=p.user.id, vm_id=vm.id,
                         expires_at=utcnow() + dt.timedelta(seconds=ttl)))
    log_event(db, "vm.console", user=p.user, tenant_id=vm.tenant_id, target_type="vm", target_id=vm.id,
              target_name=vm.hv_name, ip=p.ip)
    db.commit()
    return {"ticket": ticket, "vm": vm.hv_name, "expires_in": ttl}
