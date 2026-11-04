from __future__ import annotations

from typing import Any

from ..models import VM, Cluster, Datastore, Event, Host, Iso, Network, Snapshot, StorageClass, Tenant, User
from ..services.rbac import Principal
from ..utils import format_vlan_ranges


def _iso(ts: Any) -> str | None:
    return ts.isoformat() if ts else None


def host_to_dict(h: Host) -> dict[str, Any]:
    return {
        "id": str(h.id), "name": h.name, "fqdn": h.fqdn, "address": h.address, "state": h.state,
        "in_cluster": h.in_cluster, "maintenance": h.maintenance, "reachable": h.reachable,
        "os_version": h.os_version, "logical_cpus": h.logical_cpus, "memory_total_mb": h.memory_total_mb,
        "memory_free_mb": h.memory_free_mb, "vm_count": h.vm_count, "switches": h.switches or [],
        "last_error": h.last_error, "last_seen_at": _iso(h.last_seen_at),
    }


def storage_class_to_dict(sc: StorageClass) -> dict[str, Any]:
    return {"id": str(sc.id), "name": sc.name, "description": sc.description, "tier": sc.tier}


def datastore_to_dict(d: Datastore) -> dict[str, Any]:
    return {
        "id": str(d.id), "cluster_id": str(d.cluster_id), "name": d.name, "kind": d.kind, "path": d.path,
        "discovered": d.discovered, "present": d.present, "capacity_gb": d.capacity_gb, "free_gb": d.free_gb,
        "enabled": d.enabled,
        "storage_class_id": str(d.storage_class_id) if d.storage_class_id else None,
        "storage_class": d.storage_class.name if d.storage_class else None,
    }


def cluster_to_dict(c: Cluster, detail: bool = False) -> dict[str, Any]:
    active = [h for h in c.hosts if h.in_cluster]
    out: dict[str, Any] = {
        "id": str(c.id), "name": c.name, "kind": c.kind, "description": c.description,
        "cluster_name": c.cluster_name, "cluster_fqdn": c.cluster_fqdn, "cluster_address": c.cluster_address,
        "domain": c.domain, "seed_address": c.seed_address, "username": c.username,
        "winrm_transport": c.winrm_transport, "winrm_port": c.winrm_port, "winrm_auth": c.winrm_auth,
        "winrm_cert_validation": c.winrm_cert_validation,
        "console_username": c.console_username or "", "has_console_password": bool(c.console_password_enc),
        "console_port": c.console_port, "vswitch_name": c.vswitch_name, "ha_enabled": c.ha_enabled,
        "vm_folder": c.vm_folder, "status": c.status, "status_message": c.status_message,
        "last_refresh_at": _iso(c.last_refresh_at), "created_at": _iso(c.created_at),
        "hosts_total": len(active), "hosts_up": sum(1 for h in active if h.state in ("Up", "Online")),
        "logical_cpus": sum(h.logical_cpus or 0 for h in active),
        "memory_total_mb": sum(h.memory_total_mb or 0 for h in active),
        "memory_free_mb": sum(h.memory_free_mb or 0 for h in active),
    }
    if detail:
        out["hosts"] = [host_to_dict(h) for h in c.hosts]
        out["datastores"] = [datastore_to_dict(d) for d in c.datastores]
    return out


def tenant_to_dict(t: Tenant, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "id": str(t.id), "slug": t.slug, "name": t.name, "description": t.description,
        "cluster_id": str(t.cluster_id), "cluster_name": t.cluster.name if t.cluster else None,
        "enabled": t.enabled, "quota_vcpu": t.quota_vcpu, "quota_memory_mb": t.quota_memory_mb,
        "quota_vms": t.quota_vms, "quota_iso_gb": t.quota_iso_gb, "quota_snapshots": t.quota_snapshots,
        "vlans": format_vlan_ranges([v.vlan_id for v in t.vlans]),
        "vlan_count": len(t.vlans),
        "storage_quotas": [
            {"storage_class_id": str(q.storage_class_id), "name": q.storage_class.name if q.storage_class else "",
             "limit_gb": q.limit_gb}
            for q in t.storage_quotas
        ],
        "created_at": _iso(t.created_at),
    }
    if extra:
        out.update(extra)
    return out


def network_to_dict(n: Network, nic_count: int = 0, on_router: bool = False) -> dict[str, Any]:
    return {"id": str(n.id), "name": n.name, "vlan_id": n.vlan_id, "description": n.description, "cidr": n.cidr,
            "gateway": n.gateway, "dns": n.dns, "nic_count": nic_count, "on_router": on_router,
            "created_at": _iso(n.created_at)}


def iso_to_dict(i: Iso) -> dict[str, Any]:
    return {"id": str(i.id), "name": i.name, "file_name": i.file_name, "description": i.description,
            "size_bytes": i.size_bytes, "uploaded_bytes": i.uploaded_bytes, "status": i.status,
            "public": i.tenant_id is None, "created_at": _iso(i.created_at)}


def snapshot_to_dict(s: Snapshot) -> dict[str, Any]:
    return {"id": str(s.id), "name": s.name, "description": s.description, "status": s.status,
            "created_at": _iso(s.created_at)}


VM_ACTIONS = {
    "vm.power": ["start", "shutdown", "poweroff", "reset", "reboot"],
    "vm.snapshot": ["snapshot"],
    "vm.media": ["media"],
    "vm.console": ["console"],
    "vm.edit": ["edit"],
    "vm.delete": ["delete"],
}


def vm_to_dict(vm: VM, principal: Principal | None = None, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(vm.id), "name": vm.name, "kind": vm.kind, "description": vm.description, "hv_name": vm.hv_name,
        "tenant_id": str(vm.tenant_id), "tenant": vm.tenant.slug if vm.tenant else None,
        "cluster_id": str(vm.cluster_id), "cluster": vm.cluster.name if vm.cluster else None,
        "host": vm.host.name if vm.host else None, "generation": vm.generation, "secure_boot": vm.secure_boot,
        "vcpu": vm.vcpu, "memory_mb": vm.memory_mb, "status": vm.status, "power_state": vm.power_state,
        "ha": bool(vm.cluster_group), "guest_ips": vm.guest_ips or [], "cpu_usage": vm.cpu_usage,
        "memory_assigned_mb": vm.memory_assigned_mb, "uptime_s": vm.uptime_s, "last_error": vm.last_error,
        "iso": {"id": str(vm.iso.id), "name": vm.iso.name} if vm.iso else None,
        "disk_gb": sum(d.size_gb for d in vm.disks), "snapshot_count": len(vm.snapshots),
        "last_sync_at": _iso(vm.last_sync_at), "created_at": _iso(vm.created_at),
    }
    if principal is not None and not principal.is_provider:
        perms = principal.vm_perms(vm.id)
        out["permissions"] = sorted(perms)
    if detail:
        out["hv_id"] = vm.hv_id
        out["disks"] = [{"id": str(d.id), "position": d.position, "size_gb": d.size_gb,
                         "storage_class_id": str(d.storage_class_id),
                         "storage_class": d.storage_class.name if d.storage_class else None,
                         "datastore": d.datastore.name if d.datastore else None} for d in vm.disks]
        out["nics"] = [{"id": str(n.id), "position": n.position, "name": n.hv_name, "mac": n.mac,
                        "network_id": str(n.network_id) if n.network_id else None,
                        "network": n.network.name if n.network else None,
                        "vlan_id": n.network.vlan_id if n.network else None} for n in vm.nics]
        out["snapshots"] = [snapshot_to_dict(s) for s in vm.snapshots]
    return out


def user_to_dict(u: User) -> dict[str, Any]:
    return {
        "id": str(u.id), "username": u.username, "full_name": u.full_name, "email": u.email,
        "is_active": u.is_active, "locale": u.locale, "tenant_id": str(u.tenant_id) if u.tenant_id else None,
        "locked": bool(u.locked_until), "last_login_at": _iso(u.last_login_at), "created_at": _iso(u.created_at),
        "roles": [{"id": str(r.id), "role": r.role, "vm_id": str(r.vm_id) if r.vm_id else None,
                   "vm_name": r.vm.name if r.vm else None} for r in u.roles],
    }


def event_to_dict(e: Event) -> dict[str, Any]:
    return {"id": e.id, "ts": _iso(e.ts), "username": e.username, "action": e.action,
            "target_type": e.target_type, "target_id": e.target_id, "target_name": e.target_name,
            "status": e.status, "details": e.details, "ip": e.ip,
            "tenant_id": str(e.tenant_id) if e.tenant_id else None}


# ---------------------------------------------------------------------------
# Virtual routers
# ---------------------------------------------------------------------------


def extnet_to_dict(n, allocated: int = 0) -> dict[str, Any]:  # noqa: ANN001
    import ipaddress as _ip

    net = _ip.IPv4Network(n.cidr, strict=False)
    usable = max(0, net.num_addresses - (2 if net.prefixlen < 31 else 0) - (1 if n.gateway else 0))
    return {"id": str(n.id), "name": n.name, "purpose": n.purpose, "vlan_id": n.vlan_id, "cidr": n.cidr,
            "gateway": n.gateway, "dns": n.dns, "vswitch_name": n.vswitch_name, "description": n.description,
            "allocated": allocated, "usable": usable, "created_at": _iso(n.created_at)}


def extip_to_dict(i, tenant_slug: str | None = None, used_by: list[str] | None = None) -> dict[str, Any]:  # noqa: ANN001
    return {"id": str(i.id), "network_id": str(i.network_id), "network": i.network.name, "purpose": i.network.purpose,
            "vlan_id": i.network.vlan_id, "cidr": i.network.cidr, "address": i.address,
            "tenant_id": str(i.tenant_id), "tenant": tenant_slug, "description": i.description,
            "used_by": used_by or [], "created_at": _iso(i.created_at)}


def ipset_to_dict(s, used_by: list[str] | None = None) -> dict[str, Any]:  # noqa: ANN001
    return {"id": str(s.id), "name": s.name, "description": s.description, "entries": s.entries,
            "used_by": used_by or [], "created_at": _iso(s.created_at)}


def fwrule_to_dict(r) -> dict[str, Any]:  # noqa: ANN001
    return {"id": str(r.id), "position": r.position, "name": r.name, "enabled": r.enabled, "action": r.action,
            "protocol": r.protocol, "sources": r.sources, "destinations": r.destinations, "src_ports": r.src_ports,
            "dst_ports": r.dst_ports, "log": r.log, "description": r.description}


def natrule_to_dict(r) -> dict[str, Any]:  # noqa: ANN001
    return {"id": str(r.id), "position": r.position, "name": r.name, "enabled": r.enabled, "kind": r.kind,
            "external_ip_id": str(r.external_ip_id), "external_ip": r.external_ip.address if r.external_ip else None,
            "internal_address": r.internal_address, "protocol": r.protocol, "external_port": r.external_port,
            "internal_port": r.internal_port, "match_address": r.match_address, "log": r.log,
            "description": r.description}


def router_to_dict(r, provider: bool = False, wan: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ANN001
    vm = r.vm
    out: dict[str, Any] = {
        "id": str(r.id), "name": r.name, "status": r.status, "status_message": r.status_message,
        "tenant_id": str(r.tenant_id), "tenant": r.tenant.slug if r.tenant else None,
        "firewall_stateful": r.firewall_stateful, "firewall_default_action": r.firewall_default_action,
        "firewall_log_default": r.firewall_log_default, "config_version": r.config_version,
        "applied_version": r.applied_version, "last_applied_at": _iso(r.last_applied_at), "last_error": r.last_error,
        "storage_class": r.storage_class.name if r.storage_class else None,
        "networks": [{"id": str(rn.network.id), "name": rn.network.name, "vlan_id": rn.network.vlan_id,
                      "cidr": rn.network.cidr, "gateway": rn.network.gateway}
                     for rn in sorted(r.networks, key=lambda x: x.network.vlan_id)],
        "vm": {"id": str(vm.id), "status": vm.status, "power_state": vm.power_state, "vcpu": vm.vcpu,
               "memory_mb": vm.memory_mb, "disk_gb": sum(d.size_gb for d in vm.disks)} if vm else None,
        "wan": wan, "rules": len(r.firewall_rules), "nat_rules": len(r.nat_rules), "created_at": _iso(r.created_at),
    }
    if provider:
        out["mgmt_ip"] = r.mgmt_ip.address if r.mgmt_ip else None
        out["mgmt_vlan"] = r.mgmt_ip.network.vlan_id if r.mgmt_ip else None
        out["vm"] = out["vm"] and {**out["vm"], "hv_name": vm.hv_name, "host": vm.host.name if vm.host else None,
                                   "cluster": vm.cluster.name if vm.cluster else None, "hv_id": vm.hv_id}
    return out
