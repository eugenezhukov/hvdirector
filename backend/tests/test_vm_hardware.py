"""VM hardware changes (disks, memory, vCPU, adapters), provider VM management and VM load graphs."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid

import pytest

from hvdirector.services.metrics import compute_sample

from .conftest import Api, run_tasks


def _fake_state() -> dict:
    with open(os.environ["HVD_FAKE_STATE"]) as fh:
        return json.load(fh)


def _fake_vm(hv_name: str) -> dict:
    return next(v for v in _fake_state()["vms"].values() if v["name"] == hv_name)


def _code(r) -> str:  # noqa: ANN001
    return r.json()["detail"]["code"]


@pytest.fixture(scope="module")
def env(app):
    prov = Api(app)
    prov.login("admin", "Admin123!")
    gold = prov.post("/api/provider/storage-classes", {"name": "HW Gold", "tier": 1}).json()
    silver = prov.post("/api/provider/storage-classes", {"name": "HW Silver", "tier": 2}).json()
    disc = prov.post("/api/provider/clusters/discover", {"address": "10.40.0.10", "username": "LAB\\svc",
                                                         "password": "x", "winrm_auth": "ntlm"}).json()
    cl = prov.post("/api/provider/clusters", {
        "name": "HW cluster", "kind": "cluster", "seed_address": "10.40.0.10", "username": "LAB\\svc",
        "password": "x", "vswitch_name": "vSwitch-Tenants", "ha_enabled": True,
        "nodes": [{"name": n["name"], "address": n["address"]} for n in disc["nodes"]],
        "datastores": [{"name": "csv1", "path": disc["csvs"][0]["path"], "kind": "csv", "storage_class_id": gold["id"]}],
    }).json()
    run_tasks()
    t = prov.post("/api/provider/tenants", {
        "slug": "hwco", "name": "HW Co", "cluster_id": cl["cluster"]["id"], "quota_vcpu": 8, "quota_memory_mb": 16384,
        "quota_vms": 3, "quota_snapshots": 2, "vlans": "500-502",
        "storage_quotas": [{"storage_class_id": gold["id"], "limit_gb": 100}],
        "admin_username": "hwadmin", "admin_password": "Hw12345678!"}).json()
    ten = Api(app)
    ten.login("hwadmin", "Hw12345678!", org="hwco")
    net = ten.post("/api/tenant/networks", {"name": "lan", "cidr": "10.50.0.0/24", "gateway": "10.50.0.1"}).json()
    r = ten.post("/api/tenant/vms", {"name": "app01", "vcpu": 2, "memory_mb": 2048, "power_on": True,
                                     "disks": [{"size_gb": 20, "storage_class_id": gold["id"]},
                                               {"size_gb": 10, "storage_class_id": gold["id"]}],
                                     "nics": [{"network_id": net["id"]}]})
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    vm = ten.get(f"/api/tenant/vms/{r.json()['vm']['id']}").json()
    assert vm["power_state"] == "Running"
    return {"prov": prov, "t": ten, "gold": gold["id"], "silver": silver["id"], "net": net["id"], "vm": vm,
            "tenant": t["id"], "app": app}


def _vm(env) -> dict:  # noqa: ANN001
    return env["t"].get(f"/api/tenant/vms/{env['vm']['id']}").json()


def _reconf(env, body: dict, api: Api | None = None):  # noqa: ANN001, ANN202
    api = api or env["t"]
    prefix = "/api/provider" if api is env["prov"] else "/api/tenant"
    return api.post(f"{prefix}/vms/{env['vm']['id']}/reconfigure", body)


def _storage_used(env) -> int:  # noqa: ANN001
    return env["t"].get("/api/tenant/summary").json()["quotas"]["storage"][0]["used_gb"]


def test_01_extend_and_remove_disks(env):
    vm = _vm(env)
    system, data = vm["disks"]
    assert _storage_used(env) == 30
    # the system disk of a generation 2 VM is extended while the VM runs
    r = _reconf(env, {"disk_ops": [{"op": "resize", "disk_id": system["id"], "size_gb": 32}]})
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    vm = _vm(env)
    assert vm["disks"][0]["size_gb"] == 32 and _storage_used(env) == 42
    fake = _fake_vm(vm["hv_name"])
    assert sorted(d["size_gb"] for d in fake["disks"]) == [10, 32]
    # validation
    assert _code(_reconf(env, {"disk_ops": [{"op": "resize", "disk_id": system["id"], "size_gb": 32}]})) == "disk_shrink"
    assert _code(_reconf(env, {"disk_ops": [{"op": "resize", "disk_id": data["id"], "size_gb": 99}]})) == \
        "quota_exceeded"
    assert _code(_reconf(env, {"disk_ops": [{"op": "remove", "disk_id": system["id"]}]})) == "system_disk"
    assert _code(_reconf(env, {"disk_ops": [{"op": "remove", "disk_id": data["id"]},
                                            {"op": "resize", "disk_id": data["id"], "size_gb": 20}]})) == \
        "duplicate_disk_op"
    assert _code(_reconf(env, {"disk_ops": [{"op": "remove", "disk_id": str(uuid.uuid4())}]})) == "not_found"
    assert _code(_reconf(env, {"add_disks": [{"size_gb": 5, "storage_class_id": env["silver"]}]})) == \
        "storage_class_not_allowed"
    # not while the VM has snapshots
    assert env["t"].post(f"/api/tenant/vms/{vm['id']}/snapshots", {"name": "before"}).status_code == 200
    run_tasks()
    assert _code(_reconf(env, {"disk_ops": [{"op": "remove", "disk_id": data["id"]}]})) == "vm_has_snapshots"
    snap = _vm(env)["snapshots"][0]
    assert env["t"].delete(f"/api/tenant/vms/{vm['id']}/snapshots/{snap['id']}").status_code == 200
    run_tasks()
    # removing a data disk frees the quota after the task succeeds
    r = _reconf(env, {"disk_ops": [{"op": "remove", "disk_id": data["id"]}]})
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    vm = _vm(env)
    assert [d["size_gb"] for d in vm["disks"]] == [32] and _storage_used(env) == 32
    assert [d["size_gb"] for d in _fake_vm(vm["hv_name"])["disks"]] == [32]


def test_02_failed_change_rolls_back(env):
    vm = _vm(env)
    # a checkpoint that HV Director does not know about makes Hyper-V refuse the disk change
    path = os.environ["HVD_FAKE_STATE"]
    st = _fake_state()
    fake = next(v for v in st["vms"].values() if v["name"] == vm["hv_name"])
    fake["snapshots"].append({"id": str(uuid.uuid4()).upper(), "name": "outside", "created": "2026-01-01T00:00:00Z"})
    with open(path, "w") as fh:
        json.dump(st, fh)
    r = _reconf(env, {"memory_mb": 3072, "disk_ops": [{"op": "resize", "disk_id": vm["disks"][0]["id"], "size_gb": 40}]})
    assert r.status_code == 200, r.text
    res = run_tasks()[-1]
    assert res["status"] == "error" and "checkpoint" in res["message"].lower()
    vm = _vm(env)
    assert vm["disks"][0]["size_gb"] == 32 and vm["memory_mb"] == 2048 and _storage_used(env) == 32
    st = _fake_state()
    next(v for v in st["vms"].values() if v["name"] == vm["hv_name"])["snapshots"] = []
    with open(path, "w") as fh:
        json.dump(st, fh)


def test_03_memory_cpu_and_adapters(env):
    vm = _vm(env)
    assert vm["power_state"] == "Running"
    # memory: runtime resize of a running VM; vCPU: VM must be off
    r = _reconf(env, {"memory_mb": 4096})
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    assert _vm(env)["memory_mb"] == 4096 and _fake_vm(vm["hv_name"])["memory_mb"] == 4096
    assert _code(_reconf(env, {"vcpu": 4})) == "vm_must_be_off"
    assert _code(_reconf(env, {"memory_mb": 4096})) == "nothing_to_change"
    # adapters: hot add / remove on generation 2
    r = _reconf(env, {"nics": [{"op": "add", "network_id": env["net"]}, {"op": "add"}]})
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    nics = _vm(env)["nics"]
    assert [(n["name"], n["network"]) for n in nics] == [("nic0", "lan"), ("nic1", "lan"), ("nic2", None)]
    assert len(_fake_vm(vm["hv_name"])["nics"]) == 3
    r = _reconf(env, {"nics": [{"op": "remove", "nic_id": nics[2]["id"]}, {"op": "set", "nic_id": nics[1]["id"]}]})
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    assert [(n["name"], n["network"]) for n in _vm(env)["nics"]] == [("nic0", "lan"), ("nic1", None)]
    assert _code(_reconf(env, {"nics": [{"op": "add"}] * 7})) == "too_many_nics"


def test_04_provider_manages_tenant_vms(env):
    prov, t = env["prov"], env["t"]
    vm_id = env["vm"]["id"]
    d = prov.get(f"/api/provider/vms/{vm_id}").json()
    assert d["hv_name"] == "hwco_app01" and [n["name"] for n in d["options"]["networks"]] == ["lan"]
    assert [(c["name"], c["limit_gb"], c["used_gb"], c["available"]) for c in d["options"]["storage_classes"]] == [
        ("HW Gold", 100, 32, True)]
    # power off and change the vCPU count as the provider
    assert prov.post(f"/api/provider/vms/{vm_id}/power", {"action": "poweroff"}).status_code == 200
    assert run_tasks()[-1]["status"] == "success"
    assert t.get(f"/api/tenant/vms/{vm_id}").json()["power_state"] == "Off"
    r = _reconf(env, {"vcpu": 4, "add_disks": [{"size_gb": 8, "storage_class_id": env["gold"]}]}, api=prov)
    assert r.status_code == 200, r.text
    assert run_tasks()[-1]["status"] == "success"
    vm = t.get(f"/api/tenant/vms/{vm_id}").json()
    assert vm["vcpu"] == 4 and [d["size_gb"] for d in vm["disks"]] == [32, 8]
    # tenant quotas apply to the provider as well
    assert _code(_reconf(env, {"vcpu": 9}, api=prov)) == "quota_exceeded"
    assert prov.post(f"/api/provider/vms/{vm_id}/power", {"action": "start"}).status_code == 200
    assert run_tasks()[-1]["status"] == "success"
    # the organization sees the provider's tasks and the audit trail
    tasks = t.get(f"/api/tasks?limit=50&target_id={vm_id}").json()
    assert {"vm.power", "vm.reconfigure"} <= {x["kind"] for x in tasks if x["username"] == "admin"}
    events = prov.get("/api/events?limit=50").json()["items"]
    ev = next(e for e in events if e["action"] == "vm.reconfigure" and e["target_name"] == "app01")
    assert ev["details"]["by"] == "provider" and ev["username"] == "admin"
    # tenant users cannot use the provider API, unknown power actions are rejected
    assert t.post(f"/api/provider/vms/{vm_id}/power", {"action": "start"}).status_code == 403
    assert _code(prov.post(f"/api/provider/vms/{vm_id}/power", {"action": "explode"})) == "invalid_action"
    assert prov.get(f"/api/provider/vms/{uuid.uuid4()}").status_code == 404


def test_05_metrics_rates():
    base = {"state": "Running", "vcpu": 2, "cpu_usage": 3, "mem_assigned_mb": 4096, "mem_demand_mb": 1500}
    first = dict(base, cpu={"run": 1_000_000_000, "n": 2, "ts": 50_000_000_000},
                 disk={"read_mb": 100, "write_mb": 10, "io": 1000}, net={"rx_mb": 5, "tx_mb": 1})
    sample, state = compute_sample(None, first, "HV01", 1000.0, 32)
    assert sample["cpu_pct"] is None and sample["disk_read_bps"] is None and sample["mem_demand_mb"] == 1500
    # 60 s later: 2 vCPUs ran 30 s in total -> 25 %, 60 MB read -> 1 MB/s, 600 normalized IOs -> 10 IOPS
    second = dict(base, cpu={"run": 1_000_000_000 + 300_000_000, "n": 2, "ts": 50_000_000_000 + 600_000_000},
                  disk={"read_mb": 160, "write_mb": 10, "io": 1600}, net={"rx_mb": 65, "tx_mb": 1})
    sample, state2 = compute_sample(state, second, "HV01", 1060.0, 32)
    assert sample["cpu_pct"] == 25.0 and sample["disk_read_bps"] == 1024 * 1024 and sample["disk_write_bps"] == 0
    assert sample["disk_iops"] == 10 and sample["net_rx_bps"] == 1024 * 1024
    # live migration: other node, counters start over -> one interval without rates
    sample, _ = compute_sample(state2, dict(second, cpu={"run": 5, "n": 2, "ts": 7}), "HV02", 1120.0, 32)
    assert sample["cpu_pct"] is None and sample["disk_read_bps"] is None and sample["mem_assigned_mb"] == 4096
    # metering reset on the same node: negative deltas are dropped; an idle VM has 0 % CPU
    idle = {"run": 1_300_000_000, "n": 2, "ts": 50_000_000_000 + 1_200_000_000}
    sample, _ = compute_sample(state2, dict(second, cpu=idle, disk={"read_mb": 1, "write_mb": 1, "io": 1}), "HV01",
                               1120.0, 32)
    assert sample["disk_read_bps"] is None and sample["cpu_pct"] == 0.0 and sample["net_tx_bps"] == 0
    # no hypervisor counters: Get-VM CPUUsage (percent of the host) scaled to the VM size
    sample, _ = compute_sample(None, dict(base, cpu=None), "HV01", 1000.0, 32)
    assert sample["cpu_pct"] == 48.0
    assert compute_sample(state2, dict(second, state="Off"), "HV01", 1120.0, 32) == (None, None)


def test_06_metrics_collection_and_api(env, monkeypatch):
    from hvdirector.db import SessionLocal
    from hvdirector.hyperv import get_driver
    from hvdirector.models import VmMetric
    from hvdirector.services import metrics

    vm_id = env["vm"]["id"]
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    db = SessionLocal()
    try:
        for _ in range(4):
            metrics.collect_all(db, get_driver())
            clock[0] += 60
        rows = db.query(VmMetric).filter(VmMetric.vm_id == uuid.UUID(vm_id)).order_by(VmMetric.ts).all()
        assert len(rows) == 4 and rows[0].cpu_pct is None and rows[0].mem_assigned_mb == 4096
        for r in rows[1:]:
            assert 0 < r.cpu_pct <= 100 and r.disk_read_bps > 0 and r.net_rx_bps > 0 and r.disk_iops > 0
        # a gap of 10 minutes (VM off) becomes a null point; old samples are purged
        db.add(VmMetric(vm_id=uuid.UUID(vm_id), ts=rows[-1].ts + dt.timedelta(minutes=10), cpu_pct=50.0))
        db.add(VmMetric(vm_id=uuid.UUID(vm_id), ts=rows[0].ts - dt.timedelta(days=30), cpu_pct=1.0))
        db.commit()
        assert metrics.purge(db, 14) == 1
        db.commit()
    finally:
        db.close()
        monkeypatch.undo()
    data = env["t"].get(f"/api/tenant/vms/{vm_id}/metrics?range=1h").json()
    assert data["step_s"] == 60 and data["memory_mb"] == 4096 and data["vcpu"] == 4
    assert len(data["t"]) == 6 and data["series"]["cpu_pct"][-2] is None and data["series"]["cpu_pct"][-1] == 50.0
    assert data["latest"]["cpu_pct"] == 50.0
    week = env["prov"].get(f"/api/provider/vms/{vm_id}/metrics?range=7d").json()
    assert week["step_s"] == 1680 and len(week["t"]) >= 1
    r = env["t"].get(f"/api/tenant/vms/{vm_id}/metrics?range=2h")
    assert r.status_code == 400 and _code(r) == "invalid_value"
    # collection settings are clamped
    r = env["prov"].put("/api/provider/settings/metrics", json={"interval_s": 5, "retention_days": 0})
    assert r.status_code == 200
    s = env["prov"].get("/api/provider/settings").json()["metrics"]
    assert s == {"interval_s": 30, "retention_days": 1}
    env["prov"].put("/api/provider/settings/metrics", json={"interval_s": 60, "retention_days": 14})
