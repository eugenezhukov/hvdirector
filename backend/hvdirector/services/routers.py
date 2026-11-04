"""Virtual routers (simplified Cloud Director Edge): data assembly, validation and worker tasks."""

from __future__ import annotations

import ipaddress
import logging
import os
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..errors import AppError, Conflict, NotFound
from ..hyperv import HyperVDriver
from ..models import (VM, ExternalIp, ExternalNetwork, FirewallRule, IpSet, Iso, NatRule, Network, Router, Task,
                      User, utcnow)
from ..security import decrypt_secret, encrypt_secret
from ..utils import unique_iso_file_name
from . import netutil
from .audit import log_event
from .inventory import endpoint_for, run_on_vm
from .isostore import IsoStore
from .routerconf import FwRuleSpec, LanSpec, NatRuleSpec, RouterSpec, WanSpec, render_all
from .routerprov import InstallSpec, generate_ed25519, installer_files, remaster_iso
from .routerssh import RouterConn, get_transport
from .settings import get_setting, set_setting
from .taskqueue import enqueue
from .vmops import (ERROR_HOOKS, TaskContext, choose_host, delete_vm_resources, disk_path, handler, on_error,
                    vm_base)

log = logging.getLogger(__name__)

ROUTER_VM_NAME = "_edge"  # not a valid tenant VM name, so it never collides
FW_ACTIONS = ("allow", "drop", "reject")
FW_PROTOCOLS = ("any", "tcp", "udp", "tcp_udp", "icmp")
NAT_PROTOCOLS = ("any", "tcp", "udp", "tcp_udp")


# ---------------------------------------------------------------------------
# Keys and lookups
# ---------------------------------------------------------------------------


def ensure_keypair(db: Session) -> tuple[str, str]:
    """HV Director's SSH key used to manage all routers (generated once, private part encrypted)."""
    s = get_setting(db, "router")
    if s.get("ssh_private_key_enc") and s.get("ssh_public_key"):
        return decrypt_secret(s["ssh_private_key_enc"]), s["ssh_public_key"]
    private, public = generate_ed25519("hvdirector")
    set_setting(db, "router", {"ssh_private_key_enc": encrypt_secret(private), "ssh_public_key": public})
    db.flush()
    return private, public


def tenant_wan(db: Session, tenant_id: uuid.UUID) -> tuple[ExternalNetwork | None, list[ExternalIp]]:
    ips = db.scalars(select(ExternalIp).join(ExternalNetwork, ExternalNetwork.id == ExternalIp.network_id)
                     .where(ExternalIp.tenant_id == tenant_id, ExternalNetwork.purpose == "wan")
                     .order_by(ExternalIp.created_at)).all()
    ips = sorted(ips, key=lambda i: ipaddress.IPv4Address(i.address))
    return (ips[0].network if ips else None), list(ips)


def tenant_mgmt_ip(db: Session, tenant_id: uuid.UUID) -> ExternalIp | None:
    return db.scalar(select(ExternalIp).join(ExternalNetwork, ExternalNetwork.id == ExternalIp.network_id)
                     .where(ExternalIp.tenant_id == tenant_id, ExternalNetwork.purpose == "mgmt"))


def router_conn(db: Session, router: Router) -> RouterConn:
    private, _ = ensure_keypair(db)
    return RouterConn(host=router.mgmt_ip.address, private_key=private, host_key_pub=router.host_key_pub)


def _dns_list(value: str) -> list[str]:
    return [d for d in (value or "").replace(";", ",").replace(" ", ",").split(",") if d]


def _allowed_mgmt(settings: dict[str, Any]) -> list[str]:
    items = _dns_list(settings.get("mgmt_allowed", ""))
    return [str(n.network_address) if n.prefixlen == 32 else str(n) for n in netutil.normalize_addresses(items)]


# ---------------------------------------------------------------------------
# Rule resolution (DB -> render spec)
# ---------------------------------------------------------------------------


def _resolve_items(items: list[dict[str, Any]], nets: dict[str, Network],
                   ipsets: dict[str, IpSet]) -> list[ipaddress.IPv4Network] | None:
    if not items:
        return None  # any
    values: list[str] = []
    for it in items:
        t, v = it.get("type"), str(it.get("value", ""))
        if t == "cidr":
            values.append(v)
        elif t == "ipset":
            s = ipsets.get(v)
            if s is not None:
                values.extend(s.entries)
        elif t == "network":
            n = nets.get(v)
            if n is not None and n.cidr:
                values.append(n.cidr)
    return netutil.normalize_addresses(values)


def build_spec(db: Session, router: Router) -> RouterSpec:
    tenant = router.tenant
    settings = get_setting(db, "router")
    mgmt = router.mgmt_ip
    mnet = netutil.parse_cidr(mgmt.network.cidr, "network")
    wan_net, wan_ips = tenant_wan(db, tenant.id)
    wan = None
    if wan_net is not None and wan_ips:
        wnet = netutil.parse_cidr(wan_net.cidr, "network")
        wan = WanSpec(vlan=wan_net.vlan_id, addresses=[f"{i.address}/{wnet.prefixlen}" for i in wan_ips],
                      gateway=wan_net.gateway or None)
    lans = []
    for rn in sorted(router.networks, key=lambda x: x.network.vlan_id):
        n = rn.network
        net = netutil.parse_cidr(n.cidr, "network")
        lans.append(LanSpec(vlan=n.vlan_id, address=f"{n.gateway}/{net.prefixlen}", network=str(net), name=n.name))
    nets = {str(n.id): n for n in db.scalars(select(Network).where(Network.tenant_id == tenant.id))}
    ipsets = {str(s.id): s for s in db.scalars(select(IpSet).where(IpSet.tenant_id == tenant.id))}
    fw = []
    for r in router.firewall_rules:
        if not r.enabled:
            continue
        fw.append(FwRuleSpec(key=str(r.id)[:8], name=r.name, action=r.action, protocol=r.protocol,
                             src=_resolve_items(r.sources, nets, ipsets), dst=_resolve_items(r.destinations, nets, ipsets),
                             src_ports=netutil.parse_ports(r.src_ports), dst_ports=netutil.parse_ports(r.dst_ports),
                             log=r.log))
    nat = []
    for r in router.nat_rules:
        if not r.enabled:
            continue
        nat.append(NatRuleSpec(key=str(r.id)[:8], name=r.name, kind=r.kind, external_ip=r.external_ip.address,
                               internal=r.internal_address, protocol=r.protocol,
                               external_ports=netutil.parse_ports(r.external_port),
                               internal_port=int(r.internal_port) if r.internal_port else None,
                               match=r.match_address or None, log=r.log))
    return RouterSpec(
        hostname=f"{tenant.slug}-edge", mgmt_mac=router.mgmt_mac, wan_mac=router.wan_mac, lan_mac=router.lan_mac,
        mgmt_address=f"{mgmt.address}/{mnet.prefixlen}", mgmt_gateway=mgmt.network.gateway or None,
        mgmt_dns=_dns_list(mgmt.network.dns), mgmt_allowed=_allowed_mgmt(settings), wan=wan, lans=lans,
        stateful=router.firewall_stateful, default_action=router.firewall_default_action,
        log_default=router.firewall_log_default, fw_rules=fw, nat_rules=nat)


def install_spec(db: Session, router: Router) -> InstallSpec:
    settings = get_setting(db, "router")
    _, public = ensure_keypair(db)
    mgmt = router.mgmt_ip
    mnet = netutil.parse_cidr(mgmt.network.cidr, "network")
    keys = [public] + [k for k in (settings.get("authorized_keys") or "").splitlines() if k.strip()]
    return InstallSpec(
        hostname=f"{router.tenant.slug}-edge", mgmt_ip=mgmt.address, mgmt_prefix=mnet.prefixlen,
        mgmt_gateway=mgmt.network.gateway or None, dns=_dns_list(mgmt.network.dns) or ([mgmt.network.gateway]
                                                                                          if mgmt.network.gateway else []),
        root_password=decrypt_secret(router.root_password_enc), authorized_keys=keys,
        host_key_private=decrypt_secret(router.host_key_enc), host_key_public=router.host_key_pub,
        mirror_host=settings.get("mirror_host") or "", mirror_dir=settings.get("mirror_dir") or "/debian",
        http_proxy=settings.get("http_proxy") or "", timezone=settings.get("timezone") or "Etc/UTC")


# ---------------------------------------------------------------------------
# Validation of user input
# ---------------------------------------------------------------------------


def validate_networks_for_router(db: Session, tenant_id: uuid.UUID, network_ids: list[uuid.UUID]) -> list[Network]:
    nets = []
    for nid in dict.fromkeys(network_ids):
        n = db.get(Network, nid)
        if n is None or n.tenant_id != tenant_id:
            raise NotFound("network")
        if not n.cidr or not n.gateway:
            raise AppError("network_needs_gateway", "Set the subnet (CIDR) and gateway of the network first",
                           network=n.name)
        netutil.parse_interface(n.gateway, n.cidr)
        nets.append(n)
    parsed = [(n, netutil.parse_cidr(n.cidr, "network")) for n in nets]
    wan_net, _ = tenant_wan(db, tenant_id)
    for i, (a, na) in enumerate(parsed):
        for b, nb in parsed[i + 1:]:
            if na.overlaps(nb):
                raise Conflict("networks_overlap", "Connected networks overlap", a=a.name, b=b.name)
        if wan_net is not None and na.overlaps(netutil.parse_cidr(wan_net.cidr, "network")):
            raise Conflict("network_overlaps_wan", "Network overlaps the external (WAN) network", network=a.name)
    return nets


def _validate_items(db: Session, tenant_id: uuid.UUID, items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for it in items or []:
        t, v = it.get("type"), str(it.get("value", "")).strip()
        if t == "cidr":
            netutil.parse_address_item(v)
        elif t == "ipset":
            s = db.get(IpSet, uuid.UUID(v)) if v else None
            if s is None or s.tenant_id != tenant_id:
                raise NotFound("ip_set")
        elif t == "network":
            n = db.get(Network, uuid.UUID(v)) if v else None
            if n is None or n.tenant_id != tenant_id:
                raise NotFound("network")
            if not n.cidr:
                raise AppError("network_needs_gateway", "The network has no subnet (CIDR)", network=n.name)
        else:
            raise AppError("invalid_rule_item", "Unknown address item type", type=str(t))
        out.append({"type": t, "value": v})
    if len(out) > 50:
        raise AppError("too_many_addresses", "Too many address items", max=50)
    return out


def validate_fw_rule(db: Session, tenant_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name or len(name) > 64:
        raise AppError("invalid_rule_name", "Rule name is required (up to 64 characters)")
    action = data.get("action", "allow")
    protocol = data.get("protocol", "any")
    if action not in FW_ACTIONS or protocol not in FW_PROTOCOLS:
        raise AppError("invalid_rule", "Invalid action or protocol")
    src_ports = netutil.format_ports(netutil.parse_ports(data.get("src_ports")))
    dst_ports = netutil.format_ports(netutil.parse_ports(data.get("dst_ports")))
    if (src_ports or dst_ports) and protocol not in ("tcp", "udp", "tcp_udp"):
        raise AppError("ports_need_protocol", "Ports can be set only for TCP/UDP")
    return {
        "name": name, "action": action, "protocol": protocol, "enabled": bool(data.get("enabled", True)),
        "sources": _validate_items(db, tenant_id, data.get("sources")),
        "destinations": _validate_items(db, tenant_id, data.get("destinations")),
        "src_ports": src_ports, "dst_ports": dst_ports, "log": bool(data.get("log", False)),
        "description": (data.get("description") or "")[:1000],
    }


def validate_nat_rule(db: Session, router: Router, data: dict[str, Any]) -> dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name or len(name) > 64:
        raise AppError("invalid_rule_name", "Rule name is required (up to 64 characters)")
    kind = data.get("kind")
    protocol = data.get("protocol", "any")
    if kind not in ("snat", "dnat") or protocol not in NAT_PROTOCOLS:
        raise AppError("invalid_rule", "Invalid NAT type or protocol")
    ext = db.get(ExternalIp, uuid.UUID(str(data.get("external_ip_id")))) if data.get("external_ip_id") else None
    if ext is None or ext.tenant_id != router.tenant_id or ext.network.purpose != "wan":
        raise NotFound("external_ip")
    internal = str(data.get("internal_address") or "").strip()
    match = str(data.get("match_address") or "").strip()
    ext_port = netutil.parse_ports(data.get("external_port"))
    int_port = str(data.get("internal_port") or "").strip()
    if kind == "snat":
        nets = netutil.parse_address_item(internal)
        if len(nets) != 1:
            raise AppError("invalid_address", "SNAT source must be one IP or CIDR", value=internal)
        internal = str(nets[0]) if nets[0].prefixlen < 32 else str(nets[0].network_address)
        if ext_port or int_port:
            raise AppError("ports_not_allowed", "Port translation is configured with DNAT rules")
    else:
        internal = str(netutil.parse_ip(internal, "internal_address"))
        if ext_port and protocol == "any":
            raise AppError("ports_need_protocol", "Ports can be set only for TCP/UDP")
        if len(ext_port) > 1:
            raise AppError("invalid_port", "DNAT external port: one port or one range", value=str(data.get("external_port")))
        if int_port:
            p = netutil.parse_ports(int_port)
            if len(p) != 1 or p[0][0] != p[0][1]:
                raise AppError("invalid_port", "Internal port must be a single port", value=int_port)
            if protocol == "any":
                raise AppError("ports_need_protocol", "Ports can be set only for TCP/UDP")
            if not ext_port or ext_port[0][0] != ext_port[0][1]:
                raise AppError("invalid_port", "Port translation needs a single external port", value=int_port)
            int_port = str(p[0][0])
    if match:
        nets = netutil.parse_address_item(match)
        if len(nets) != 1:
            raise AppError("invalid_address", "Match address must be one IP or CIDR", value=match)
        match = str(nets[0]) if nets[0].prefixlen < 32 else str(nets[0].network_address)
    return {
        "name": name, "kind": kind, "protocol": protocol, "enabled": bool(data.get("enabled", True)),
        "external_ip_id": ext.id, "internal_address": internal,
        "external_port": netutil.format_ports(ext_port) if kind == "dnat" else "", "internal_port": int_port,
        "match_address": match, "log": bool(data.get("log", False)),
        "description": (data.get("description") or "")[:1000],
    }


def validate_ipset_entries(entries: list[str]) -> list[str]:
    clean = [e.strip() for e in entries if e and e.strip()]
    for e in clean:
        netutil.parse_address_item(e)
    if len(clean) > 500:
        raise AppError("too_many_addresses", "Too many entries", max=500)
    return clean


def rules_referencing(db: Session, tenant_id: uuid.UUID, kind: str, value: str) -> list[str]:
    """Names of firewall rules that reference an IP set or a network."""
    router = db.scalar(select(Router).where(Router.tenant_id == tenant_id))
    if router is None:
        return []
    names = []
    for r in router.firewall_rules:
        for it in list(r.sources) + list(r.destinations):
            if it.get("type") == kind and str(it.get("value")) == value:
                names.append(r.name)
                break
    return names


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------


def bump(db: Session, router: Router | None, user: User | None) -> Task | None:
    """Configuration changed: increase the version and schedule an apply (one queued at a time)."""
    if router is None:
        return None
    router.config_version += 1
    if router.status != "ready":
        return None  # applied at the end of the deployment
    queued = db.scalar(select(Task).where(Task.kind == "router.apply", Task.target_id == router.id,
                                          Task.status == "queued"))
    if queued is not None:
        return queued
    return enqueue(db, "router.apply", user=user, tenant_id=router.tenant_id, target_type="router",
                   target_id=router.id, target_name=router.name, lock_key=f"router:{router.id}")


def bump_tenant(db: Session, tenant_id: uuid.UUID, user: User | None) -> Task | None:
    return bump(db, db.scalar(select(Router).where(Router.tenant_id == tenant_id)), user)


# ---------------------------------------------------------------------------
# Worker tasks
# ---------------------------------------------------------------------------


def _router(db: Session, task: Task) -> Router:
    router = db.get(Router, task.target_id)
    if router is None:
        raise AppError("not_found", "Router no longer exists")
    return router


def _store(db: Session) -> IsoStore:
    return IsoStore.from_setting(get_setting(db, "iso_share"))


def apply_router(db: Session, driver: HyperVDriver, router: Router, ctx: TaskContext | None = None) -> str:
    vm = router.vm
    if vm is None or not vm.hv_id:
        raise AppError("router_not_deployed", "Router VM is not deployed")
    version = router.config_version
    spec = build_spec(db, router)
    cluster = vm.cluster
    wan_net, wan_ips = tenant_wan(db, router.tenant_id)
    lan_vlans = [lan.vlan for lan in spec.lans]
    nic_ops = [
        {"op": "set", "name": "wan", "switch": (wan_net.vswitch_name or cluster.vswitch_name) if wan_net else None,
         "vlan": wan_net.vlan_id if wan_net else None, "connected": bool(wan_net and wan_ips)},
        {"op": "set", "name": "lan", "switch": cluster.vswitch_name, "mode": "trunk",
         "allowed": ",".join(str(v) for v in lan_vlans), "connected": bool(lan_vlans)},
    ]
    if ctx:
        ctx.progress(30, "hyper-v adapters")
    run_on_vm(db, driver, vm, lambda ep: driver.reconfigure(ep, vm.hv_id, {"nics": nic_ops}))
    if ctx:
        ctx.progress(60, "router OS configuration")
    out = get_transport().apply(router_conn(db, router), render_all(spec))
    router.applied_version = version
    router.last_applied_at = utcnow()
    router.last_error = ""
    return out


@handler("router.deploy")
def router_deploy(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    router = _router(db, task)
    vm = router.vm
    if vm is None:
        raise AppError("router_not_deployed", "Router VM record is missing")
    cluster = vm.cluster
    settings = get_setting(db, "router")
    iso = db.get(Iso, uuid.UUID(settings["iso_id"])) if settings.get("iso_id") else None
    if iso is None or iso.status != "ready":
        raise AppError("router_iso_missing", "The provider has not selected a Debian 13 ISO for routers")
    ensure_keypair(db)
    db.commit()
    cfg = get_config()
    store = _store(db)

    # 1. Installer ISO: cached copy of the provider ISO + preseed and router configuration
    ctx.progress(5, "building installer ISO")
    cache_dir = os.path.join(cfg.data_dir, "cache")
    tmp_dir = os.path.join(cfg.data_dir, "tmp")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    src_local = os.path.join(cache_dir, iso.file_name)
    if not os.path.exists(src_local) or os.path.getsize(src_local) != iso.size_bytes:
        store.download(iso.file_name, src_local + ".part")
        os.replace(src_local + ".part", src_local)
    out_name = unique_iso_file_name(f"router-{router.tenant.slug}.iso")
    out_local = os.path.join(tmp_dir, out_name)
    try:
        remaster_iso(src_local, out_local, installer_files(build_spec(db, router), install_spec(db, router)),
                     work_dir=tmp_dir)
        ctx.progress(12, "uploading installer ISO")
        store.upload(out_local, out_name)
    finally:
        if os.path.exists(out_local):
            os.remove(out_local)
    router.install_iso_file = out_name
    db.commit()

    # 2. VM: management NIC connected (installer uses it), WAN/LAN disconnected until configured
    ctx.progress(18, "creating VM")
    host = choose_host(db, driver, cluster, vm.memory_mb)
    vm.host_id = host.id
    disk = vm.disks[0]
    disk.path = disk_path(vm, disk)
    mnet = router.mgmt_ip.network
    spec_vm = {
        "name": vm.hv_name, "generation": 2, "vcpu": vm.vcpu, "memory_mb": vm.memory_mb,
        "path": vm_base(vm, disk.datastore.path),
        "notes": f"HV Director virtual router | tenant: {router.tenant.slug} | router: {router.id}",
        "secure_boot": "linux", "iso": store.host_path(out_name), "boot_disk_first": True,
        "disks": [{"path": disk.path, "size_gb": disk.size_gb}],
        "nics": [
            {"name": "mgmt", "switch": mnet.vswitch_name or cluster.vswitch_name, "vlan": mnet.vlan_id,
             "mac": router.mgmt_mac},
            {"name": "wan", "switch": None, "connected": False, "mac": router.wan_mac},
            {"name": "lan", "switch": None, "connected": False, "mac": router.lan_mac},
        ],
    }
    ep = endpoint_for(cluster, host)
    res = driver.create_vm(ep, spec_vm)
    vm.hv_id = str(res["id"]).upper()
    db.commit()
    if cluster.kind == "cluster" and cluster.ha_enabled:
        vm.cluster_group = (driver.make_ha(ep, vm.hv_id, vm.hv_name).get("group") or vm.hv_name)
        db.commit()
    driver.vm_power(ep, vm.hv_id, "start")
    vm.power_state = "Running"
    db.commit()

    # 3. Unattended installation; the router answers over SSH when done
    ctx.progress(25, "installing OS")
    timeout = int(settings.get("install_timeout_min") or 45) * 60

    def _progress(msg: str) -> None:
        ctx.progress(30, msg)

    get_transport().wait_ready(router_conn(db, router), timeout, _progress)

    # 4. Eject and remove the installer (it contains the root password and host key)
    ctx.progress(80, "cleanup installer")
    run_on_vm(db, driver, vm, lambda e: driver.set_media(e, vm.hv_id, None))
    store.delete(out_name)
    router.install_iso_file = None
    vm.status = "ready"
    db.commit()

    # 5. Hyper-V adapters (WAN VLAN, LAN trunk) and full configuration
    apply_router(db, driver, router, ctx)
    router.status = "ready"
    router.status_message = ""
    log_event(db, "router.deployed", username=task.username, tenant_id=router.tenant_id, target_type="router",
              target_id=router.id, target_name=router.name, details={"host": host.name})
    return {"router_id": str(router.id), "host": host.name}


@on_error("router.deploy")
def router_deploy_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    router = db.get(Router, task.target_id)
    if router is None:
        return
    router.status = "error"
    router.status_message = str(exc)[:4000]
    if router.vm is not None:
        router.vm.status = "error"
        router.vm.last_error = str(exc)[:4000]
    log_event(db, "router.deployed", username=task.username, tenant_id=router.tenant_id, target_type="router",
              target_id=router.id, target_name=router.name, status="failure", details={"error": str(exc)[:1000]})


@handler("router.apply")
def router_apply(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    router = _router(db, task)
    if router.status not in ("ready", "error") or router.vm is None or not router.vm.hv_id:
        raise AppError("router_not_ready", "Router is not deployed")
    if router.applied_version >= router.config_version and not task.params.get("force"):
        return {"skipped": True, "version": router.applied_version}
    apply_router(db, driver, router, ctx)
    if router.status == "error" and router.vm.status == "ready":
        router.status = "ready"
        router.status_message = ""
    return {"version": router.applied_version}


@on_error("router.apply")
def router_apply_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    router = db.get(Router, task.target_id)
    if router is not None:
        router.last_error = str(exc)[:4000]


@handler("router.delete")
def router_delete(db: Session, driver: HyperVDriver, task: Task, ctx: TaskContext) -> dict[str, Any]:
    router = _router(db, task)
    vm = router.vm
    if vm is not None and vm.hv_id:
        delete_vm_resources(db, driver, vm)
    if router.install_iso_file:
        try:
            _store(db).delete(router.install_iso_file)
        except AppError as exc:
            log.warning("Cannot delete installer ISO %s: %s", router.install_iso_file, exc)
    tenant_id, name, rid = router.tenant_id, router.name, router.id
    db.delete(router)
    db.flush()
    if vm is not None:
        db.delete(vm)
    log_event(db, "router.deleted", username=task.username, tenant_id=tenant_id, target_type="router",
              target_id=rid, target_name=name)
    return {"deleted": True}


@on_error("router.delete")
def router_delete_failed(db: Session, driver: HyperVDriver, task: Task, exc: Exception) -> None:
    router = db.get(Router, task.target_id)
    if router is not None:
        router.status = "error"
        router.status_message = str(exc)[:4000]


__all__ = ["ERROR_HOOKS", "ROUTER_VM_NAME", "apply_router", "bump", "bump_tenant", "build_spec", "ensure_keypair",
           "tenant_mgmt_ip", "tenant_wan", "validate_fw_rule", "validate_nat_rule", "validate_ipset_entries",
           "validate_networks_for_router", "rules_referencing", "VM"]
