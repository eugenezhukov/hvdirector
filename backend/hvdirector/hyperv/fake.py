"""In-process Hyper-V simulator for development, demos and automated tests.

Enable with HVD_HYPERV_DRIVER=fake.  State is kept in a JSON file
(HVD_FAKE_STATE) so the API process and the worker process share it.

Topology rules for the "Add cluster" wizard:
  * an address whose last octet is >= 200, or that starts with "standalone",
    becomes a standalone host;
  * any other new address becomes a 3-node failover cluster: the entered
    address is the cluster IP and the nodes get the next three addresses.
  * password "wrong" simulates an authentication failure.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import fcntl
import ipaddress
import json
import os
import random
import time
import uuid
from collections.abc import Iterator
from typing import Any

from .base import Endpoint, HyperVDriver, HyperVError


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _mac() -> str:
    return "00155D" + "".join(random.choice("0123456789ABCDEF") for _ in range(6))


class FakeDriver(HyperVDriver):
    name = "fake"

    def __init__(self, state_file: str, delay: float | None = None) -> None:
        self.state_file = state_file
        self.lock_file = state_file + ".lock"
        self.delay = float(os.environ.get("HVD_FAKE_DELAY", "0.5")) if delay is None else delay
        os.makedirs(os.path.dirname(os.path.abspath(state_file)) or ".", exist_ok=True)

    # ----------------------------------------------------------------- state
    @contextlib.contextmanager
    def _state(self, write: bool = False) -> Iterator[dict[str, Any]]:
        with open(self.lock_file, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            try:
                try:
                    with open(self.state_file, encoding="utf-8") as fh:
                        state = json.load(fh)
                except (FileNotFoundError, json.JSONDecodeError):
                    state = {"clusters": {}, "hosts": {}, "vms": {}, "seq": 0}
                yield state
                if write:
                    tmp = self.state_file + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(state, fh, indent=1)
                    os.replace(tmp, self.state_file)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _sleep(self, factor: float = 1.0) -> None:
        if self.delay > 0:
            time.sleep(self.delay * factor)

    # -------------------------------------------------------------- topology
    @staticmethod
    def _find_host_by_address(state: dict[str, Any], address: str) -> dict[str, Any] | None:
        for h in state["hosts"].values():
            if h["address"] == address or h["name"].lower() == address.lower() or h["fqdn"].lower() == address.lower():
                return h
        return None

    @staticmethod
    def _find_cluster_by_address(state: dict[str, Any], address: str) -> dict[str, Any] | None:
        for c in state["clusters"].values():
            if c["address"] == address or c["name"].lower() == address.lower() or c["fqdn"].lower() == address.lower():
                return c
        return None

    def _new_host(self, state: dict[str, Any], name: str, address: str, cluster: str | None) -> dict[str, Any]:
        host = {
            "name": name,
            "fqdn": f"{name.lower()}.lab.local",
            "address": address,
            "cluster": cluster,
            "state": "Up",
            "logical_cpus": 64,
            "memory_total_mb": 262144,
            "switches": [{"name": "vSwitch-Tenants", "type": "External"}],
            "volumes": [{"path": "C:\\", "label": "System", "size_gb": 200.0, "free_gb": 120.0},
                        {"path": "D:\\", "label": "VMs", "size_gb": 2000.0, "free_gb": 1500.0}],
        }
        state["hosts"][name] = host
        return host

    def _ensure_topology(self, state: dict[str, Any], address: str) -> None:
        if self._find_host_by_address(state, address) or self._find_cluster_by_address(state, address):
            return
        state["seq"] = state.get("seq", 0) + 1
        seq = state["seq"]
        try:
            ip = ipaddress.ip_address(address)
            last = int(str(ip).split(".")[-1]) if ip.version == 4 else 0
        except ValueError:
            ip = None
            last = 0
        standalone = address.lower().startswith("standalone") or last >= 200
        if standalone:
            name = f"HVSA{seq:02d}"
            self._new_host(state, name, address, None)
            return
        cname = f"HVCL{seq:02d}"
        base = ip if ip is not None and ip.version == 4 else ipaddress.ip_address(f"10.99.{seq}.10")
        nodes = []
        for i in range(1, 4):
            nname = f"{cname}-N{i}"
            self._new_host(state, nname, str(base + i), cname)
            nodes.append(nname)
        state["clusters"][cname] = {
            "name": cname,
            "domain": "lab.local",
            "fqdn": f"{cname.lower()}.lab.local",
            "address": address if ip is not None else str(base),
            "nodes": nodes,
            "csvs": [
                {"name": "Cluster Disk 1", "path": "C:\\ClusterStorage\\Volume1", "size_gb": 4096.0, "free_gb": 3600.0},
                {"name": "Cluster Disk 2", "path": "C:\\ClusterStorage\\Volume2", "size_gb": 8192.0, "free_gb": 8000.0},
            ],
        }

    def _connect(self, state: dict[str, Any], ep: Endpoint) -> dict[str, Any]:
        """Resolve an endpoint to a host record, simulating connection errors."""
        if ep.password == "wrong":
            raise HyperVError("Authentication failed: the user name or password is incorrect", "AUTH_FAILED",
                              ep.display)
        host = self._find_host_by_address(state, ep.address)
        if host is None:
            cluster = self._find_cluster_by_address(state, ep.address)
            if cluster:
                for n in cluster["nodes"]:
                    if state["hosts"][n]["state"] == "Up":
                        host = state["hosts"][n]
                        break
        if host is None:
            raise HyperVError(f"Cannot connect to {ep.address}:{ep.port}: connection refused", "CONNECT_FAILED",
                              ep.display)
        if host["state"] != "Up":
            raise HyperVError(f"Cannot connect to {ep.address}:{ep.port}: host is down", "CONNECT_FAILED",
                              ep.display)
        return host

    def _host_info(self, state: dict[str, Any], host: dict[str, Any]) -> dict[str, Any]:
        vms = [v for v in state["vms"].values() if v["host"] == host["name"]]
        used = sum(v["memory_mb"] for v in vms if v["state"] == "Running")
        return {
            "name": host["name"],
            "fqdn": host["fqdn"],
            "domain": "lab.local",
            "part_of_domain": True,
            "os": "Microsoft Windows Server 2022 Datacenter (10.0.20348) [simulated]",
            "logical_cpus": host["logical_cpus"],
            "memory_total_mb": host["memory_total_mb"],
            "memory_free_mb": max(0, host["memory_total_mb"] - 8192 - used),
            "vm_count": len(vms),
            "vm_path": "C:\\ProgramData\\Microsoft\\Windows\\Hyper-V",
            "switches": host["switches"],
            "volumes": host["volumes"],
            "addresses": [host["address"]],
        }

    def _cluster_info(self, state: dict[str, Any], cname: str) -> dict[str, Any]:
        c = state["clusters"][cname]
        return {
            "name": c["name"],
            "domain": c["domain"],
            "fqdn": c["fqdn"],
            "address": c["address"],
            "nodes": [
                {"name": n, "state": state["hosts"][n]["state"], "id": str(i + 1),
                 "addresses": [state["hosts"][n]["address"]]}
                for i, n in enumerate(c["nodes"])
            ],
            "csvs": [dict(csv, owner=c["nodes"][0], state="Online") for csv in c["csvs"]],
        }

    # -------------------------------------------------------------- inventory
    def ping(self, ep: Endpoint) -> dict[str, Any]:
        with self._state() as st:
            host = self._connect(st, ep)
            return {"name": host["name"], "hyperv": True, "ps_version": "5.1"}

    def discover(self, ep: Endpoint) -> dict[str, Any]:
        self._sleep()
        with self._state(write=True) as st:
            if ep.password != "wrong":
                self._ensure_topology(st, ep.address)
            host = self._connect(st, ep)
            result: dict[str, Any] = {"is_cluster": bool(host["cluster"]), "host": self._host_info(st, host)}
            if host["cluster"]:
                result["cluster"] = self._cluster_info(st, host["cluster"])
            return result

    def host_info(self, ep: Endpoint) -> dict[str, Any]:
        with self._state() as st:
            return self._host_info(st, self._connect(st, ep))

    def host_memory(self, ep: Endpoint) -> dict[str, Any]:
        info = self.host_info(ep)
        return {"memory_free_mb": info["memory_free_mb"], "memory_total_mb": info["memory_total_mb"]}

    def cluster_info(self, ep: Endpoint) -> dict[str, Any]:
        with self._state() as st:
            host = self._connect(st, ep)
            if not host["cluster"]:
                raise HyperVError("The cluster service is not running on this host", "NOT_CLUSTERED", ep.display)
            return self._cluster_info(st, host["cluster"])

    def node_states(self, ep: Endpoint) -> dict[str, Any]:
        info = self.cluster_info(ep)
        return {"nodes": [{"name": n["name"], "state": n["state"]} for n in info["nodes"]]}

    @staticmethod
    def _summary(vm: dict[str, Any]) -> dict[str, Any]:
        running = vm["state"] == "Running"
        uptime = int(time.time() - vm.get("started_ts", time.time())) if running else 0
        return {
            "id": vm["id"],
            "name": vm["name"],
            "state": vm["state"],
            "cpu_usage": random.randint(1, 25) if running else 0,
            "memory_assigned_mb": vm["memory_mb"] if running else 0,
            "uptime_s": uptime,
            "clustered": vm.get("clustered", False),
            "ips": [f"192.168.{vm['nics'][0]['vlan'] % 250}.{10 + int(vm['id'][:2], 16) % 200}"]
            if running and vm["nics"] and vm["nics"][0].get("vlan") else [],
            "nics": [{"name": n["name"], "mac": n["mac"]} for n in vm["nics"]],
            "host": vm["host"],
        }

    def list_vms(self, ep: Endpoint) -> dict[str, Any]:
        with self._state() as st:
            host = self._connect(st, ep)
            return {"host": host["name"],
                    "vms": [self._summary(v) for v in st["vms"].values() if v["host"] == host["name"]]}

    def test_path(self, ep: Endpoint, path: str) -> dict[str, Any]:
        with self._state() as st:
            host = self._connect(st, ep)
            return {"path": path, "exists": True, "host": host["name"]}

    def find_owner(self, ep: Endpoint, group: str | None, vm_id: str | None) -> dict[str, Any]:
        with self._state() as st:
            self._connect(st, ep)
            for vm in st["vms"].values():
                if (group and vm.get("group") == group) or (vm_id and vm["id"] == vm_id):
                    if not vm.get("group"):
                        break
                    return {"group": vm["group"], "owner": vm["host"], "state": "Online"}
            raise HyperVError(f"Cluster role for VM {vm_id} not found", "GROUP_NOT_FOUND", ep.display)

    # -------------------------------------------------------------------- VMs
    def _get_vm(self, st: dict[str, Any], host: dict[str, Any], vm_id: str) -> dict[str, Any]:
        vm = st["vms"].get(vm_id)
        if vm is None or vm["host"] != host["name"]:
            raise HyperVError(f"VM {vm_id} is not registered on host {host['name']}", "VM_NOT_FOUND", host["name"])
        return vm

    def create_vm(self, ep: Endpoint, spec: dict[str, Any]) -> dict[str, Any]:
        self._sleep(3)
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            for v in st["vms"].values():
                if v["host"] == host["name"] and v["name"] == spec["name"]:
                    raise HyperVError(f"VM '{spec['name']}' already exists on {host['name']}", "VM_EXISTS",
                                      host["name"])
            vm_id = str(uuid.uuid4()).upper()
            nics = [{"name": n["name"], "mac": (n.get("mac") or _mac()).upper(), "switch": n.get("switch"),
                     "vlan": n.get("vlan"), "mode": n.get("mode") or "access", "allowed": n.get("allowed"),
                     "connected": n.get("connected") is not False and bool(n.get("switch"))}
                    for n in spec.get("nics", [])]
            vm = {
                "id": vm_id,
                "name": spec["name"],
                "host": host["name"],
                "state": "Off",
                "generation": spec["generation"],
                "vcpu": spec["vcpu"],
                "memory_mb": spec["memory_mb"],
                "disks": [{"path": d["path"], "size_gb": d["size_gb"]} for d in spec.get("disks", [])],
                "nics": nics,
                "dvd": spec.get("iso"),
                "snapshots": [],
                "group": None,
                "clustered": False,
                "config_path": spec["path"] + "\\" + spec["name"],
                "boot_disk_first": bool(spec.get("boot_disk_first")),
            }
            st["vms"][vm_id] = vm
            return {"id": vm_id, "name": vm["name"], "state": "Off", "config_path": vm["config_path"],
                    "nics": [{"name": n["name"], "mac": n["mac"]} for n in nics], "host": host["name"]}

    def make_ha(self, ep: Endpoint, vm_id: str, group: str) -> dict[str, Any]:
        self._sleep()
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            vm = self._get_vm(st, host, vm_id)
            if not host["cluster"]:
                raise HyperVError("Host is not a cluster member", "NOT_CLUSTERED", host["name"])
            vm["group"] = group
            vm["clustered"] = True
            return {"group": group, "owner": host["name"]}

    def vm_power(self, ep: Endpoint, vm_id: str, action: str) -> dict[str, Any]:
        self._sleep(2)
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            vm = self._get_vm(st, host, vm_id)
            state = vm["state"]
            if action == "start":
                if state != "Running":
                    vm["state"] = "Running"
                    vm["started_ts"] = time.time()
            elif action in ("shutdown", "poweroff"):
                vm["state"] = "Off"
            elif action in ("reset", "reboot"):
                if state != "Running":
                    raise HyperVError("VM is not running", "VM_NOT_RUNNING", host["name"])
                vm["started_ts"] = time.time()
            elif action == "save":
                if state == "Running":
                    vm["state"] = "Saved"
            elif action == "resume":
                vm["state"] = "Running"
            else:
                raise HyperVError(f"Unknown power action {action}", "BAD_ACTION", host["name"])
            return self._summary(vm)

    def vm_info(self, ep: Endpoint, vm_id: str) -> dict[str, Any]:
        with self._state() as st:
            host = self._connect(st, ep)
            vm = self._get_vm(st, host, vm_id)
            info = self._summary(vm)
            info.update({
                "snapshots": copy.deepcopy(vm["snapshots"]),
                "nics": [dict(n, ips=[]) for n in vm["nics"]],
                "dvd": vm.get("dvd"),
                "heartbeat": "OkApplicationsHealthy" if vm["state"] == "Running" else "",
                "generation": vm["generation"],
                "vcpu": vm["vcpu"],
                "memory_startup_mb": vm["memory_mb"],
            })
            return info

    def delete_vm(self, ep: Endpoint, args: dict[str, Any]) -> dict[str, Any]:
        self._sleep(2)
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            vm_id = args.get("id")
            vm = st["vms"].get(vm_id) if vm_id else None
            if vm is not None:
                if vm["host"] != host["name"]:
                    raise HyperVError(f"VM {vm_id} is not registered on host {host['name']}", "VM_NOT_FOUND",
                                      host["name"])
                del st["vms"][vm_id]
            return {"removed": list(args.get("disk_paths") or []) + list(args.get("folders") or [])}

    def snapshot(self, ep: Endpoint, vm_id: str, action: str, name: str | None = None,
                 snapshot_id: str | None = None) -> dict[str, Any]:
        self._sleep(1.5)
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            vm = self._get_vm(st, host, vm_id)
            if action == "create":
                snap = {"id": str(uuid.uuid4()).upper(), "name": name or "checkpoint", "created": _now(),
                        "parent": vm["snapshots"][-1]["id"] if vm["snapshots"] else ""}
                vm["snapshots"].append(snap)
                return {"id": snap["id"], "name": snap["name"], "created": snap["created"]}
            snap = next((s for s in vm["snapshots"] if s["id"] == snapshot_id), None)
            if action == "revert":
                if snap is None:
                    raise HyperVError(f"Checkpoint {snapshot_id} not found", "SNAPSHOT_NOT_FOUND", host["name"])
                vm["state"] = "Off"
                return {"state": vm["state"]}
            if action == "delete":
                if snap is not None:
                    vm["snapshots"].remove(snap)
                return {"deleted": snap is not None}
            raise HyperVError(f"Unknown snapshot action {action}", "BAD_ACTION", host["name"])

    def set_media(self, ep: Endpoint, vm_id: str, iso_path: str | None) -> dict[str, Any]:
        self._sleep()
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            vm = self._get_vm(st, host, vm_id)
            vm["dvd"] = iso_path
            return {"dvd": iso_path or ""}

    def reconfigure(self, ep: Endpoint, vm_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self._sleep()
        with self._state(write=True) as st:
            host = self._connect(st, ep)
            vm = self._get_vm(st, host, vm_id)
            if (changes.get("vcpu") or changes.get("memory_mb")) and vm["state"] != "Off":
                raise HyperVError("Power off the VM to change vCPU or memory", "VM_MUST_BE_OFF", host["name"])
            if changes.get("vcpu"):
                vm["vcpu"] = int(changes["vcpu"])
            if changes.get("memory_mb"):
                vm["memory_mb"] = int(changes["memory_mb"])
            added = []
            for d in changes.get("add_disks") or []:
                vm["disks"].append({"path": d["path"], "size_gb": d["size_gb"]})
                added.append(d["path"])
            nic_out = []
            for n in changes.get("nics") or []:
                if n["op"] == "add":
                    nic = {"name": n["name"], "mac": (n.get("mac") or _mac()).upper(), "switch": n.get("switch"),
                           "vlan": n.get("vlan"), "mode": n.get("mode") or "access", "allowed": n.get("allowed"),
                           "connected": n.get("connected") is not False and bool(n.get("switch"))}
                    vm["nics"].append(nic)
                    nic_out.append({"name": nic["name"], "mac": nic["mac"]})
                elif n["op"] == "set":
                    found = False
                    for nic in vm["nics"]:
                        if nic["name"] == n["name"]:
                            found = True
                            nic["switch"] = n.get("switch")
                            nic["vlan"] = n.get("vlan")
                            nic["mode"] = n.get("mode") or "access"
                            nic["allowed"] = n.get("allowed")
                            nic["connected"] = n.get("connected") is not False and bool(n.get("switch"))
                    if not found:
                        raise HyperVError(f"Network adapter {n['name']} not found", "PS_ERROR", host["name"])
                elif n["op"] == "remove":
                    vm["nics"] = [x for x in vm["nics"] if x["name"] != n["name"]]
            res = self._summary(vm)
            res.update({"added_disks": added, "nics": nic_out})
            return res

    def console_connection(self, host_address: str, port: int, vm_hv_id: str, username: str,
                           password: str, domain: str) -> tuple[str, dict[str, str]]:
        target = os.environ.get("HVD_FAKE_VNC", "127.0.0.1:5900")
        vhost, _, vport = target.rpartition(":")
        return "vnc", {"hostname": vhost or "127.0.0.1", "port": vport or "5900",
                       "password": os.environ.get("HVD_FAKE_VNC_PASSWORD", "")}

    # ------------------------------------------------------- simulator controls
    def sim_add_node(self, cluster: str, name: str, address: str) -> None:
        with self._state(write=True) as st:
            c = st["clusters"][cluster]
            self._new_host(st, name, address, cluster)
            c["nodes"].append(name)

    def sim_remove_node(self, cluster: str, name: str) -> None:
        with self._state(write=True) as st:
            c = st["clusters"][cluster]
            c["nodes"] = [n for n in c["nodes"] if n != name]
            st["hosts"].pop(name, None)
            for vm in st["vms"].values():
                if vm["host"] == name and c["nodes"]:
                    vm["host"] = c["nodes"][0]

    def sim_set_node_state(self, name: str, state: str) -> None:
        with self._state(write=True) as st:
            st["hosts"][name]["state"] = state

    def sim_migrate(self, vm_name: str, target_host: str) -> None:
        with self._state(write=True) as st:
            for vm in st["vms"].values():
                if vm["name"] == vm_name:
                    vm["host"] = target_host
