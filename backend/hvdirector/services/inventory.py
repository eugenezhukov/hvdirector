"""Inventory of Hyper-V clusters / hosts: discovery, refresh, VM state sync, owner lookup."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..hyperv import Endpoint, HyperVDriver, HyperVError
from ..models import VM, Cluster, Datastore, Host, utcnow
from ..security import decrypt_secret

log = logging.getLogger(__name__)

UP_STATES = {"Up", "Online"}


def endpoint_for(cluster: Cluster, host: Host | None = None, address: str | None = None) -> Endpoint:
    addr = address or (host.address if host else cluster.seed_address)
    return Endpoint(
        address=addr,
        username=cluster.username,
        password=decrypt_secret(cluster.password_enc),
        transport=cluster.winrm_transport,
        port=cluster.winrm_port,
        auth=cluster.winrm_auth,
        cert_validation=cluster.winrm_cert_validation,
        label=host.name if host else addr,
    )


def usable_hosts(cluster: Cluster) -> list[Host]:
    return [h for h in cluster.hosts if h.in_cluster and h.state in UP_STATES]


def first_reachable(driver: HyperVDriver, cluster: Cluster, fn_name: str) -> tuple[Any, Host | None, list[str]]:
    """Run a cluster-level read call on the first host that answers."""
    errors: list[str] = []
    tried: set[str] = set()
    hosts = sorted([h for h in cluster.hosts if h.in_cluster], key=lambda h: (h.state not in UP_STATES, h.name))
    candidates: list[tuple[str, Host | None]] = [(h.address, h) for h in hosts]
    candidates.append((cluster.seed_address, None))
    if cluster.cluster_address:
        candidates.append((cluster.cluster_address, None))
    for address, host in candidates:
        if not address or address in tried:
            continue
        tried.add(address)
        try:
            result = getattr(driver, fn_name)(endpoint_for(cluster, host, address))
            return result, host, errors
        except HyperVError as exc:
            errors.append(str(exc))
            if exc.code == "AUTH_FAILED":
                break
    return None, None, errors


# --------------------------------------------------------------------------
# Wizard helpers
# --------------------------------------------------------------------------


def discovery_summary(driver: HyperVDriver, ep: Endpoint) -> dict[str, Any]:
    """Probe the seed address and, for clusters, every node."""
    data = driver.discover(ep)
    host = data["host"]
    result: dict[str, Any] = {
        "kind": "cluster" if data.get("is_cluster") else "standalone",
        "seed_host": host,
        "domain": host.get("domain"),
    }
    if data.get("is_cluster"):
        c = data["cluster"]
        nodes = []
        for n in c.get("nodes", []):
            addresses = [a for a in (n.get("addresses") or []) if a]
            address = addresses[0] if addresses else n["name"]
            node = {"name": n["name"], "state": n.get("state"), "address": address, "addresses": addresses,
                    "reachable": False, "error": "", "info": None}
            if (n.get("state") or "") in UP_STATES:
                probe = Endpoint(address=address, username=ep.username, password=ep.password,
                                 transport=ep.transport, port=ep.port, auth=ep.auth,
                                 cert_validation=ep.cert_validation, label=n["name"])
                try:
                    node["info"] = driver.host_info(probe)
                    node["reachable"] = True
                except HyperVError as exc:
                    node["error"] = str(exc)
            nodes.append(node)
        result["cluster"] = {"name": c.get("name"), "fqdn": c.get("fqdn"), "address": c.get("address"),
                             "domain": c.get("domain")}
        result["nodes"] = nodes
        result["csvs"] = c.get("csvs", [])
        switch_sets = [{s["name"] for s in (n["info"] or {}).get("switches", [])} for n in nodes if n["info"]]
        result["common_switches"] = sorted(set.intersection(*switch_sets)) if switch_sets else []
        result["volumes"] = []
    else:
        result["cluster"] = None
        result["nodes"] = [{"name": host["name"], "state": "Up", "address": ep.address,
                            "addresses": host.get("addresses", []), "reachable": True, "error": "", "info": host}]
        result["csvs"] = []
        result["volumes"] = host.get("volumes", [])
        result["common_switches"] = sorted({s["name"] for s in host.get("switches", [])})
    return result


def apply_host_info(host: Host, info: dict[str, Any]) -> None:
    host.fqdn = info.get("fqdn") or host.fqdn
    host.os_version = info.get("os")
    host.logical_cpus = info.get("logical_cpus")
    host.memory_total_mb = info.get("memory_total_mb")
    host.memory_free_mb = info.get("memory_free_mb")
    host.vm_count = info.get("vm_count")
    host.switches = [s["name"] for s in info.get("switches", [])]
    host.reachable = True
    host.last_error = ""
    host.last_seen_at = utcnow()


# --------------------------------------------------------------------------
# Refresh (Inventory -> Refresh button)
# --------------------------------------------------------------------------


def _sync_csvs(db: Session, cluster: Cluster, csvs: list[dict[str, Any]]) -> list[str]:
    added: list[str] = []
    by_path = {d.path.lower().rstrip("\\"): d for d in cluster.datastores}
    seen: set[str] = set()
    for csv in csvs:
        path = (csv.get("path") or "").rstrip("\\")
        if not path:
            continue
        key = path.lower()
        seen.add(key)
        ds = by_path.get(key)
        if ds is None:
            ds = Datastore(cluster_id=cluster.id, name=csv.get("name") or path, kind="csv", path=path,
                           discovered=True, enabled=True)
            db.add(ds)
            cluster.datastores.append(ds)
            added.append(path)
        ds.present = True
        ds.capacity_gb = csv.get("size_gb")
        ds.free_gb = csv.get("free_gb")
    # Datastores configured below a CSV mount point inherit its capacity.
    for ds in cluster.datastores:
        if ds.kind != "csv":
            continue
        p = ds.path.lower().rstrip("\\")
        match = next((c for c in csvs if p.startswith((c.get("path") or "").lower().rstrip("\\"))), None)
        if match is None:
            if ds.discovered:
                ds.present = False
            continue
        ds.present = True
        ds.capacity_gb = match.get("size_gb")
        ds.free_gb = match.get("free_gb")
    return added


def refresh_cluster(db: Session, driver: HyperVDriver, cluster: Cluster) -> dict[str, Any]:
    """Re-poll cluster membership (added / removed nodes), host facts, CSVs and VM states."""
    report: dict[str, Any] = {"added": [], "removed": [], "returned": [], "warnings": [], "new_datastores": []}
    if cluster.kind == "cluster":
        info, _, errors = first_reachable(driver, cluster, "cluster_info")
        if info is None:
            cluster.status = "error"
            cluster.status_message = "; ".join(errors) or "no reachable node"
            cluster.last_refresh_at = utcnow()
            raise HyperVError(cluster.status_message, "CLUSTER_UNREACHABLE")
        cluster.cluster_name = info.get("name") or cluster.cluster_name
        cluster.cluster_fqdn = info.get("fqdn") or cluster.cluster_fqdn
        cluster.cluster_address = info.get("address") or cluster.cluster_address
        cluster.domain = info.get("domain") or cluster.domain
        by_name = {h.name.lower(): h for h in cluster.hosts}
        seen: set[str] = set()
        for n in info.get("nodes", []):
            key = n["name"].lower()
            seen.add(key)
            addresses = [a for a in (n.get("addresses") or []) if a]
            h = by_name.get(key)
            if h is None:
                h = Host(cluster_id=cluster.id, name=n["name"], address=addresses[0] if addresses else n["name"],
                         fqdn=f"{n['name']}.{cluster.domain}".lower() if cluster.domain else None,
                         state=n.get("state") or "Unknown", in_cluster=True)
                db.add(h)
                cluster.hosts.append(h)
                report["added"].append(n["name"])
            else:
                if not h.in_cluster:
                    report["returned"].append(h.name)
                h.in_cluster = True
                h.state = n.get("state") or "Unknown"
                if not h.address and addresses:
                    h.address = addresses[0]
        for h in cluster.hosts:
            if h.name.lower() not in seen and h.in_cluster:
                h.in_cluster = False
                h.state = "Removed"
                h.reachable = False
                report["removed"].append(h.name)
        report["new_datastores"] = _sync_csvs(db, cluster, info.get("csvs", []))
    db.flush()

    # Host facts
    for h in cluster.hosts:
        if not h.in_cluster:
            continue
        if cluster.kind == "cluster" and h.state not in UP_STATES:
            h.reachable = False
            continue
        try:
            apply_host_info(h, driver.host_info(endpoint_for(cluster, h)))
            if cluster.kind == "standalone":
                h.state = "Up"
        except HyperVError as exc:
            h.reachable = False
            h.last_error = str(exc)
            if cluster.kind == "standalone":
                h.state = "Down"
            report["warnings"].append(f"{h.name}: {exc}")
        if h.reachable and cluster.vswitch_name and cluster.vswitch_name not in (h.switches or []):
            report["warnings"].append(f"{h.name}: virtual switch '{cluster.vswitch_name}' not found")

    try:
        sync_vms(db, driver, cluster)
    except HyperVError as exc:  # pragma: no cover - defensive
        report["warnings"].append(str(exc))

    active = [h for h in cluster.hosts if h.in_cluster]
    down = [h for h in active if not h.reachable]
    if active and len(down) == len(active):
        cluster.status = "error"
    elif down or report["warnings"]:
        cluster.status = "warning"
    else:
        cluster.status = "ok"
    cluster.status_message = "; ".join(report["warnings"])[:2000]
    cluster.last_refresh_at = utcnow()
    report["nodes"] = len(active)
    return report


# --------------------------------------------------------------------------
# VM state synchronisation (periodic + after operations)
# --------------------------------------------------------------------------


def apply_vm_summary(vm: VM, s: dict[str, Any], host: Host | None) -> None:
    vm.power_state = s.get("state") or vm.power_state
    vm.cpu_usage = s.get("cpu_usage")
    vm.memory_assigned_mb = s.get("memory_assigned_mb")
    vm.uptime_s = s.get("uptime_s")
    vm.guest_ips = list(s.get("ips") or [])
    if host is not None:
        vm.host_id = host.id
    vm.last_sync_at = utcnow()


def sync_vms(db: Session, driver: HyperVDriver, cluster: Cluster) -> dict[str, int]:
    vms = db.scalars(select(VM).where(VM.cluster_id == cluster.id, VM.hv_id.is_not(None))).all()
    by_id = {vm.hv_id.upper(): vm for vm in vms if vm.hv_id}
    if cluster.kind == "cluster":
        states, _, _ = first_reachable(driver, cluster, "node_states")
        if states:
            by_name = {h.name.lower(): h for h in cluster.hosts}
            for n in states.get("nodes", []):
                h = by_name.get(n["name"].lower())
                if h is not None and h.in_cluster:
                    h.state = n["state"]
    found: set[str] = set()
    complete = True
    for h in cluster.hosts:
        if not h.in_cluster or (cluster.kind == "cluster" and h.state not in UP_STATES):
            continue
        try:
            res = driver.list_vms(endpoint_for(cluster, h))
        except HyperVError as exc:
            complete = False
            h.reachable = False
            h.last_error = str(exc)
            if cluster.kind == "standalone":
                h.state = "Down"
            continue
        h.reachable = True
        h.last_seen_at = utcnow()
        if cluster.kind == "standalone":
            h.state = "Up"
        h.vm_count = len(res.get("vms", []))
        for s in res.get("vms", []):
            vm = by_id.get(str(s["id"]).upper())
            if vm is not None:
                apply_vm_summary(vm, s, h)
                found.add(vm.hv_id.upper())
    missing = 0
    for key, vm in by_id.items():
        if key not in found and vm.status in ("ready",):
            vm.power_state = "Unknown"
            if complete:
                missing += 1
    return {"vms": len(by_id), "found": len(found), "missing": missing}


# --------------------------------------------------------------------------
# Locating the node that owns a VM
# --------------------------------------------------------------------------


def locate_vm(db: Session, driver: HyperVDriver, vm: VM) -> Host | None:
    cluster = vm.cluster
    if cluster.kind == "cluster" and vm.cluster_group:
        res, _, _ = _first_answer(driver, cluster, lambda ep: driver.find_owner(ep, vm.cluster_group, vm.hv_id))
        if res and res.get("owner"):
            owner = next((h for h in cluster.hosts if h.name.lower() == str(res["owner"]).lower()), None)
            if owner is not None:
                return owner
    for h in usable_hosts(cluster) or [h for h in cluster.hosts if h.in_cluster]:
        try:
            res = driver.list_vms(endpoint_for(cluster, h))
        except HyperVError:
            continue
        for s in res.get("vms", []):
            if vm.hv_id and str(s["id"]).upper() == vm.hv_id.upper():
                return h
    return None


def _first_answer(driver: HyperVDriver, cluster: Cluster, fn: Any) -> tuple[Any, Host | None, list[str]]:
    errors: list[str] = []
    for h in usable_hosts(cluster):
        try:
            return fn(endpoint_for(cluster, h)), h, errors
        except HyperVError as exc:
            errors.append(str(exc))
            if exc.code == "GROUP_NOT_FOUND":
                break
    return None, None, errors


def run_on_vm(db: Session, driver: HyperVDriver, vm: VM, fn: Any) -> Any:
    """Run fn(endpoint) on the node owning the VM, relocating once if it moved."""
    cluster = vm.cluster
    host = vm.host
    if host is None or not host.in_cluster or (cluster.kind == "cluster" and host.state not in UP_STATES):
        host = locate_vm(db, driver, vm)
        if host is None:
            raise HyperVError(f"Cannot find the host running VM {vm.name}", "VM_NOT_FOUND")
        vm.host_id = host.id
        vm.host = host
    try:
        return fn(endpoint_for(cluster, host)), host
    except HyperVError as exc:
        if exc.code not in ("VM_NOT_FOUND", "CONNECT_FAILED"):
            raise
        other = locate_vm(db, driver, vm)
        if other is None or other.id == host.id:
            raise
        log.info("VM %s moved from %s to %s", vm.hv_name, host.name, other.name)
        vm.host_id = other.id
        vm.host = other
        return fn(endpoint_for(cluster, other)), other


def stale(ts: dt.datetime | None, seconds: int) -> bool:
    return ts is None or (utcnow() - ts).total_seconds() > seconds
