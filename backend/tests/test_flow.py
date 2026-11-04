"""End-to-end scenario through the HTTP API with the Hyper-V simulator."""

from __future__ import annotations

import pytest

from .conftest import run_tasks

CONN = {"address": "10.10.0.10", "username": "LAB\\svc-hvd", "password": "P@ssw0rd", "winrm_transport": "http",
        "winrm_port": 5985, "winrm_auth": "ntlm"}


@pytest.fixture(scope="module")
def env(app):
    from .conftest import Api

    prov = Api(app)
    me = prov.login("admin", "Admin123!")
    assert me["user"]["is_provider"] is True
    state: dict = {"prov": prov}
    return state


def test_01_storage_classes(env):
    prov = env["prov"]
    gold = prov.post("/api/provider/storage-classes", {"name": "Gold SSD", "tier": 1}).json()
    bronze = prov.post("/api/provider/storage-classes", {"name": "Bronze HDD", "tier": 3}).json()
    assert prov.post("/api/provider/storage-classes", {"name": "gold ssd"}).status_code == 409
    env["gold"], env["bronze"] = gold["id"], bronze["id"]


def test_02_discover_and_add_cluster(env):
    prov = env["prov"]
    bad = prov.post("/api/provider/clusters/discover", dict(CONN, password="wrong"))
    assert bad.status_code == 502 and bad.json()["detail"]["params"]["hv_code"] == "AUTH_FAILED"
    d = prov.post("/api/provider/clusters/discover", CONN)
    assert d.status_code == 200, d.text
    disc = d.json()
    assert disc["kind"] == "cluster" and len(disc["nodes"]) == 3
    assert all(n["reachable"] for n in disc["nodes"])
    assert "vSwitch-Tenants" in disc["common_switches"]
    csvs = disc["csvs"]
    body = {
        "name": "Cluster A", "kind": "cluster", "seed_address": CONN["address"], "username": CONN["username"],
        "password": CONN["password"], "winrm_auth": "ntlm", "vswitch_name": "vSwitch-Tenants", "ha_enabled": True,
        "cluster_name": disc["cluster"]["name"], "cluster_fqdn": disc["cluster"]["fqdn"],
        "cluster_address": disc["cluster"]["address"], "domain": disc["cluster"]["domain"],
        "nodes": [{"name": n["name"], "address": n["address"]} for n in disc["nodes"]],
        "datastores": [
            {"name": csvs[0]["name"], "path": csvs[0]["path"], "kind": "csv", "storage_class_id": env["gold"],
             "discovered": True},
            {"name": csvs[1]["name"], "path": csvs[1]["path"], "kind": "csv", "storage_class_id": env["bronze"],
             "discovered": True},
        ],
    }
    r = prov.post("/api/provider/clusters", body)
    assert r.status_code == 200, r.text
    cluster = r.json()["cluster"]
    env["cluster"] = cluster["id"]
    env["fake_cluster"] = disc["cluster"]["name"]
    tasks = run_tasks()
    assert tasks and tasks[-1]["kind"] == "cluster.refresh" and tasks[-1]["status"] == "success", tasks
    c = prov.get(f"/api/provider/clusters/{cluster['id']}").json()
    assert c["status"] == "ok", c["status_message"]
    assert all(h["state"] == "Up" and h["logical_cpus"] == 64 for h in c["hosts"])
    assert {d["storage_class"] for d in c["datastores"]} == {"Gold SSD", "Bronze HDD"}


def test_03_standalone_host(env):
    prov = env["prov"]
    disc = prov.post("/api/provider/clusters/discover", dict(CONN, address="10.20.0.201")).json()
    assert disc["kind"] == "standalone" and len(disc["nodes"]) == 1 and disc["volumes"]


def test_04_create_tenant(env):
    prov = env["prov"]
    body = {"slug": "acme", "name": "ACME Corp", "cluster_id": env["cluster"], "quota_vcpu": 8,
            "quota_memory_mb": 16384, "quota_vms": 3, "quota_iso_gb": 1, "quota_snapshots": 2,
            "vlans": "100-102", "storage_quotas": [{"storage_class_id": env["gold"], "limit_gb": 100}],
            "admin_username": "boss", "admin_password": "Boss12345!"}
    r = prov.post("/api/provider/tenants", body)
    assert r.status_code == 200, r.text
    env["tenant"] = r.json()["id"]
    # VLAN overlap with another tenant is rejected
    other = dict(body, slug="globex", name="Globex", vlans="102-104", admin_username=None)
    r = prov.post("/api/provider/tenants", other)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "vlan_conflict"
    r = prov.post("/api/provider/tenants", dict(other, vlans="200-201"))
    assert r.status_code == 200
    env["tenant2"] = r.json()["id"]


def test_05_tenant_login_network_iso(env, app):
    from .conftest import Api

    t = Api(app)
    me = t.login("boss", "Boss12345!", org="acme")
    assert "vm.create" in me["permissions"]
    env["t"] = t
    assert t.get("/api/provider/tenants").status_code == 403
    net = t.post("/api/tenant/networks", {"name": "web"}).json()
    assert net["vlan_id"] == 100
    net2 = t.post("/api/tenant/networks", {"name": "db", "vlan_id": 102}).json()
    assert net2["vlan_id"] == 102
    r = t.post("/api/tenant/networks", {"name": "x", "vlan_id": 200})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "vlan_not_in_pool"
    env["net"] = net["id"]
    # ISO upload in two chunks; name gets a GUID prefix
    data = b"x" * (3 * 1024 * 1024 + 17)
    r = t.post("/api/tenant/isos", {"name": "debian-13.iso", "size_bytes": len(data)})
    assert r.status_code == 200, r.text
    iso = r.json()
    assert iso["file_name"].endswith("_debian-13.iso") and len(iso["file_name"]) > 40
    half = len(data) // 2
    assert t.put(f"/api/tenant/isos/{iso['id']}/data?offset=0", content=data[:half]).json()["status"] == "uploading"
    bad = t.put(f"/api/tenant/isos/{iso['id']}/data?offset=5", content=data[half:])
    assert bad.status_code == 409
    done = t.put(f"/api/tenant/isos/{iso['id']}/data?offset={half}", content=data[half:]).json()
    assert done["status"] == "ready" and done["uploaded_bytes"] == len(data)
    env["iso"] = iso["id"]
    # ISO quota (1 GB)
    r = t.post("/api/tenant/isos", {"name": "big.iso", "size_bytes": 2 * 1024**3})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "quota_exceeded"


def test_06_create_vm_and_quotas(env):
    t = env["t"]
    body = {"name": "web01", "vcpu": 4, "memory_mb": 8192, "disks": [{"size_gb": 60, "storage_class_id": env["gold"]}],
            "nics": [{"network_id": env["net"]}], "iso_id": env["iso"], "power_on": True}
    r = t.post("/api/tenant/vms", body)
    assert r.status_code == 200, r.text
    vm = r.json()["vm"]
    env["vm"] = vm["id"]
    assert vm["status"] == "creating"
    # Storage class without quota -> rejected; storage quota exceeded -> rejected
    r = t.post("/api/tenant/vms", dict(body, name="x1", disks=[{"size_gb": 10, "storage_class_id": env["bronze"]}]))
    assert r.json()["detail"]["code"] == "storage_class_not_allowed"
    r = t.post("/api/tenant/vms", dict(body, name="x2", vcpu=1, memory_mb=1024,
                                       disks=[{"size_gb": 50, "storage_class_id": env["gold"]}]))
    assert r.json()["detail"]["code"] == "quota_exceeded" and r.json()["detail"]["params"]["resource"].startswith("storage")
    r = t.post("/api/tenant/vms", dict(body, name="x3", vcpu=8))
    assert r.json()["detail"]["code"] == "quota_exceeded" and r.json()["detail"]["params"]["resource"] == "vcpu"
    tasks = run_tasks()
    assert tasks[-1]["kind"] == "vm.create" and tasks[-1]["status"] == "success", tasks
    assert tasks[-1]["message"] == "" and tasks[-1]["progress"] == 100
    vm = t.get(f"/api/tenant/vms/{env['vm']}").json()
    assert vm["status"] == "ready" and vm["power_state"] == "Running" and vm["ha"] is True
    assert vm["hv_name"] == "acme_web01" and vm["nics"][0]["vlan_id"] == 100 and vm["nics"][0]["mac"]
    assert vm["iso"]["id"] == env["iso"]
    # the attached ISO cannot be deleted
    assert t.delete(f"/api/tenant/isos/{env['iso']}").status_code == 409


def test_07_power_snapshots_reconfigure(env):
    t = env["t"]
    vm_id = env["vm"]
    for action, expected in (("shutdown", "Off"), ("start", "Running"), ("reboot", "Running")):
        assert t.post(f"/api/tenant/vms/{vm_id}/power", {"action": action}).status_code == 200
        res = run_tasks()
        assert res[-1]["status"] == "success", res
        assert t.get(f"/api/tenant/vms/{vm_id}").json()["power_state"] == expected
    # snapshots with quota 2
    for i in range(2):
        assert t.post(f"/api/tenant/vms/{vm_id}/snapshots", {"name": f"s{i}"}).status_code == 200
    r = t.post(f"/api/tenant/vms/{vm_id}/snapshots", {"name": "s3"})
    assert r.status_code == 409 and r.json()["detail"]["params"]["resource"] == "snapshots"
    run_tasks()
    snaps = t.get(f"/api/tenant/vms/{vm_id}").json()["snapshots"]
    assert [s["status"] for s in snaps] == ["ready", "ready"]
    assert t.post(f"/api/tenant/vms/{vm_id}/snapshots/{snaps[0]['id']}/revert").status_code == 200
    assert t.delete(f"/api/tenant/vms/{vm_id}/snapshots/{snaps[1]['id']}").status_code == 200
    run_tasks()
    vm = t.get(f"/api/tenant/vms/{vm_id}").json()
    assert len(vm["snapshots"]) == 1 and vm["power_state"] == "Off"
    # reconfigure: cpu change needs Off (it is Off after revert in the simulator)
    r = t.post(f"/api/tenant/vms/{vm_id}/reconfigure", {"vcpu": 6, "nics": [{"op": "add", "network_id": env["net"]}],
                                                        "add_disks": [{"size_gb": 20, "storage_class_id": env["gold"]}]})
    assert r.status_code == 200, r.text
    res = run_tasks()
    assert res[-1]["status"] == "success", res
    vm = t.get(f"/api/tenant/vms/{vm_id}").json()
    assert vm["vcpu"] == 6 and len(vm["nics"]) == 2 and len(vm["disks"]) == 2
    # vCPU quota 8: going to 9 fails
    r = t.post(f"/api/tenant/vms/{vm_id}/reconfigure", {"vcpu": 9})
    assert r.status_code == 409
    # media eject
    assert t.post(f"/api/tenant/vms/{vm_id}/media", {"iso_id": None}).status_code == 200
    run_tasks()
    assert t.get(f"/api/tenant/vms/{vm_id}").json()["iso"] is None


def test_08_delegated_console_user(env, app):
    from .conftest import Api

    t = env["t"]
    vm_id = env["vm"]
    r = t.post("/api/tenant/users", {"username": "operator", "password": "Oper12345!",
                                     "roles": [{"role": "console", "vm_id": vm_id}]})
    assert r.status_code == 200, r.text
    # second VM that the operator must not see
    t.post("/api/tenant/vms", {"name": "db01", "vcpu": 1, "memory_mb": 1024,
                               "disks": [{"size_gb": 10, "storage_class_id": env["gold"]}]})
    run_tasks()
    op = Api(app)
    me = op.login("operator", "Oper12345!", org="acme")
    assert "vm.create" not in me["permissions"]
    vms = op.get("/api/tenant/vms").json()
    assert [v["name"] for v in vms] == ["web01"]
    assert vms[0]["permissions"] == ["vm.console", "vm.view"]
    assert op.post(f"/api/tenant/vms/{vm_id}/power", {"action": "start"}).status_code == 403
    assert op.get("/api/tenant/networks").status_code == 403
    # console only for running VM
    assert op.post(f"/api/tenant/vms/{vm_id}/console").status_code == 409
    t.post(f"/api/tenant/vms/{vm_id}/power", {"action": "start"})
    run_tasks()
    r = op.post(f"/api/tenant/vms/{vm_id}/console")
    assert r.status_code == 200 and len(r.json()["ticket"]) > 20
    # tasks visible to operator: only its VM
    assert all(x["target_id"] == vm_id for x in op.get("/api/tasks").json())
    # last tenant admin protection
    users = t.get("/api/tenant/users").json()
    boss = next(u for u in users if u["username"] == "boss")
    r = t.patch(f"/api/tenant/users/{boss['id']}", {"roles": [{"role": "audit"}]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_tenant_admin"


def test_09_refresh_detects_node_changes(env):
    from hvdirector.config import get_config
    from hvdirector.hyperv.fake import FakeDriver

    prov = env["prov"]
    drv = FakeDriver(get_config().fake_state_file, delay=0)
    fake_cluster = env["fake_cluster"]
    drv.sim_add_node(fake_cluster, f"{fake_cluster}-N4", "10.10.0.14")
    drv.sim_remove_node(fake_cluster, f"{fake_cluster}-N3")
    prov.post(f"/api/provider/clusters/{env['cluster']}/refresh")
    res = run_tasks()
    report = res[-1]["result"]
    assert report["added"] == [f"{fake_cluster}-N4"] and report["removed"] == [f"{fake_cluster}-N3"]
    c = prov.get(f"/api/provider/clusters/{env['cluster']}").json()
    removed = next(h for h in c["hosts"] if h["name"].endswith("-N3"))
    assert removed["in_cluster"] is False and removed["state"] == "Removed"
    assert prov.delete(f"/api/provider/hosts/{removed['id']}").status_code == 200


def test_10_vm_follows_migration(env):
    """VM moved by the cluster (live migration / failover) is found on its new owner."""
    from hvdirector.config import get_config
    from hvdirector.hyperv.fake import FakeDriver

    t = env["t"]
    vm = t.get(f"/api/tenant/vms/{env['vm']}").json()
    drv = FakeDriver(get_config().fake_state_file, delay=0)
    target = f"{env['fake_cluster']}-N4"
    drv.sim_migrate(vm["hv_name"], target)
    t.post(f"/api/tenant/vms/{env['vm']}/power", {"action": "shutdown"})
    res = run_tasks()
    assert res[-1]["status"] == "success" and res[-1]["result"]["host"] == target


def test_11_delete_vm_and_events(env):
    t = env["t"]
    assert t.delete(f"/api/tenant/vms/{env['vm']}").status_code == 200
    res = run_tasks()
    assert res[-1]["kind"] == "vm.delete" and res[-1]["status"] == "success"
    assert t.get(f"/api/tenant/vms/{env['vm']}").status_code == 404
    events = t.get("/api/events?limit=200").json()["items"]
    actions = {e["action"] for e in events}
    assert {"vm.create", "vm.deleted", "auth.login", "network.created"} <= actions
    # provider sees everything, logout works
    assert env["prov"].get("/api/events").status_code == 200
    assert t.post("/api/auth/logout").status_code == 200
    assert t.get("/api/auth/me").status_code == 401


def test_12_tenant_delete_guard(env):
    prov = env["prov"]
    r = prov.delete(f"/api/provider/tenants/{env['tenant']}")
    assert r.status_code == 409  # db01 still exists
    assert prov.delete(f"/api/provider/tenants/{env['tenant2']}").status_code == 200
