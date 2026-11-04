"""Virtual router end-to-end through the API (Hyper-V simulator + fake SSH transport, real ISO remastering)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from .conftest import TMP_DIR, Api, run_tasks

pytestmark = pytest.mark.skipif(not shutil.which("xorriso"), reason="xorriso is required to build router installers")


def _fake_vm(name: str) -> dict:
    with open(os.environ["HVD_FAKE_STATE"]) as fh:
        st = json.load(fh)
    return next(v for v in st["vms"].values() if v["name"] == name)


def _router_files(mgmt_ip: str) -> str:
    return os.path.join(os.path.dirname(os.environ["HVD_FAKE_STATE"]), "fake-routers", mgmt_ip)


@pytest.fixture(scope="module")
def env(app, tmp_path_factory):
    prov = Api(app)
    prov.login("admin", "Admin123!")
    st: dict = {"prov": prov, "app": app}
    # tiny "Debian" ISO uploaded to the public catalog and selected for routers
    tree = tmp_path_factory.mktemp("iso")
    (tree / "boot" / "grub").mkdir(parents=True)
    (tree / "install.amd").mkdir()
    (tree / "boot" / "grub" / "grub.cfg").write_text("menuentry 'Install' {}\n")
    (tree / "install.amd" / "vmlinuz").write_bytes(b"k")
    iso_path = tree.parent / "debian-13.0.0-amd64-netinst.iso"
    subprocess.run(["xorriso", "-as", "mkisofs", "-o", str(iso_path), "-V", "Debian 13", "-R", str(tree)],
                   check=True, capture_output=True)
    data = iso_path.read_bytes()
    iso = prov.post("/api/provider/isos", {"name": iso_path.name, "size_bytes": len(data)}).json()
    assert prov.put(f"/api/provider/isos/{iso['id']}/data?offset=0", content=data).json()["status"] == "ready"
    r = prov.put("/api/provider/settings/router", json={"iso_id": iso["id"], "mirror_host": "", "vcpu": 1,
                                                          "memory_mb": 1024, "disk_gb": 8, "mgmt_allowed": "198.51.100.5",
                                                          "ssh_public_key": "forged"})
    assert r.status_code == 200 and "has_ssh_private_key" in r.json()
    key = prov.get("/api/provider/router-settings/ssh-key").json()
    assert key["public_key"].startswith("ssh-ed25519 ")
    st["iso_file"] = iso["file_name"]
    # infrastructure
    sc = prov.post("/api/provider/storage-classes", {"name": "Edge SSD", "tier": 1}).json()
    disc = prov.post("/api/provider/clusters/discover", {"address": "10.30.0.10", "username": "LAB\\svc",
                                                         "password": "x", "winrm_auth": "ntlm"}).json()
    cl = prov.post("/api/provider/clusters", {
        "name": "Edge cluster", "kind": "cluster", "seed_address": "10.30.0.10", "username": "LAB\\svc",
        "password": "x", "vswitch_name": "vSwitch-Tenants", "ha_enabled": True,
        "nodes": [{"name": n["name"], "address": n["address"]} for n in disc["nodes"]],
        "datastores": [{"name": "csv1", "path": disc["csvs"][0]["path"], "kind": "csv", "storage_class_id": sc["id"]}],
    }).json()
    run_tasks()
    st.update(sc=sc["id"], cluster=cl["cluster"]["id"])
    return st


def test_01_external_networks_and_allocation(env):
    prov = env["prov"]
    wan = prov.post("/api/provider/external-networks", {"name": "Internet", "purpose": "wan", "vlan_id": 3000,
                                                          "cidr": "203.0.113.0/24", "gateway": "203.0.113.1"}).json()
    mgmt = prov.post("/api/provider/external-networks", {"name": "OOB", "purpose": "mgmt", "vlan_id": 3001,
                                                           "cidr": "198.51.100.0/24", "gateway": "198.51.100.1",
                                                           "dns": "198.51.100.1"}).json()
    bad = prov.post("/api/provider/external-networks", {"name": "x", "purpose": "wan", "vlan_id": 5,
                                                          "cidr": "10.0.0.1/24"})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_cidr"
    t = prov.post("/api/provider/tenants", {"slug": "edgeco", "name": "Edge Co", "cluster_id": env["cluster"],
                                            "quota_vcpu": 4, "quota_memory_mb": 4096, "quota_vms": 2,
                                            "vlans": "300-303",
                                            "storage_quotas": [{"storage_class_id": env["sc"], "limit_gb": 100}],
                                            "admin_username": "eadmin", "admin_password": "Edge12345!"}).json()
    clash = prov.patch(f"/api/provider/tenants/{t['id']}", {"vlans": "300-303,3000"})
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "vlan_reserved_external"
    r = prov.post(f"/api/provider/tenants/{t['id']}/external-ips", {"network_id": mgmt["id"],
                                                                     "addresses": "198.51.100.10"})
    assert r.status_code == 200, r.text
    r = prov.post(f"/api/provider/tenants/{t['id']}/external-ips", {"network_id": mgmt["id"], "count": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "mgmt_ip_single"
    r = prov.post(f"/api/provider/tenants/{t['id']}/external-ips", {"network_id": wan["id"], "count": 3})
    assert [i["address"] for i in r.json()["items"]] == ["203.0.113.2", "203.0.113.3", "203.0.113.4"]
    r = prov.post(f"/api/provider/tenants/{t['id']}/external-ips", {"network_id": wan["id"], "addresses": "203.0.113.1"})
    assert r.json()["detail"]["code"] == "ip_is_gateway"
    env.update(tenant=t["id"], wan=wan["id"], mgmt=mgmt["id"])


def test_02_create_router(env):
    t = Api(env["app"])
    me = t.login("eadmin", "Edge12345!", org="edgeco")
    assert "router.manage" in me["permissions"]
    env["t"] = t
    web = t.post("/api/tenant/networks", {"name": "web", "cidr": "10.20.0.0/24", "gateway": "10.20.0.1"}).json()
    db_ = t.post("/api/tenant/networks", {"name": "db", "cidr": "10.20.1.0/24", "gateway": "10.20.1.1"}).json()
    plain = t.post("/api/tenant/networks", {"name": "isolated"}).json()
    bad = t.post("/api/tenant/networks", {"name": "bad", "cidr": "10.30.0.0/24", "gateway": "10.31.0.1"})
    assert bad.json()["detail"]["code"] == "ip_not_in_network"
    info = t.get("/api/tenant/router").json()
    assert info["router"] is None and info["mgmt_ready"] and info["iso_ready"]
    assert [i["address"] for i in info["wan"]["ips"]] == ["203.0.113.2", "203.0.113.3", "203.0.113.4"]
    r = t.post("/api/tenant/router", {"storage_class_id": env["sc"], "network_ids": [web["id"], plain["id"]]})
    assert r.json()["detail"]["code"] == "network_needs_gateway"
    r = t.post("/api/tenant/router", {"name": "edge-gw", "storage_class_id": env["sc"], "network_ids": [web["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["router"]["status"] == "deploying"
    tasks = run_tasks()
    assert tasks[-1]["kind"] == "router.deploy" and tasks[-1]["status"] == "success", tasks
    rt = t.get("/api/tenant/router").json()["router"]
    assert rt["status"] == "ready" and rt["applied_version"] == rt["config_version"]
    vm = _fake_vm("edgeco__edge")
    nics = {n["name"]: n for n in vm["nics"]}
    assert nics["mgmt"]["vlan"] == 3001 and nics["mgmt"]["connected"]
    assert nics["wan"]["vlan"] == 3000 and nics["wan"]["connected"]
    assert nics["lan"]["mode"] == "trunk" and nics["lan"]["allowed"] == "300" and nics["lan"]["connected"]
    assert vm["dvd"] is None and vm["boot_disk_first"] and vm["clustered"]
    # the per-router installer ISO (root password, host key) is removed after the installation
    files = os.listdir(os.path.join(TMP_DIR, "iso"))
    assert env["iso_file"] in files and not [f for f in files if "_router-" in f]
    nft = open(os.path.join(_router_files("198.51.100.10"), "nftables.conf")).read()
    assert 'iifname "mgmt0" ip saddr { 198.51.100.5 } tcp dport 22 accept' in nft
    net = open(os.path.join(_router_files("198.51.100.10"), "network", "42-hvd-wan0.network")).read()
    assert "Address=203.0.113.4/24" in net and "Gateway=203.0.113.1" in net
    # router counts against quotas, but is not a tenant VM
    s = t.get("/api/tenant/summary").json()
    assert s["quotas"]["vms"]["used"] == 1 and s["quotas"]["vcpu"]["used"] == 1 and s["vms_total"] == 0
    assert t.get("/api/tenant/vms").json() == []
    env.update(web=web["id"], db=db_["id"])


def test_03_rules_and_apply(env):
    t = env["t"]
    ips = t.get("/api/tenant/external-ips").json()
    ext = ips[0]["id"]
    s = t.post("/api/tenant/ip-sets", {"name": "admins", "entries": ["192.0.2.0/24", "198.51.100.77"]}).json()
    bad = t.post("/api/tenant/ip-sets", {"name": "bad", "entries": ["10.0.0.1/8"]})
    assert bad.json()["detail"]["code"] == "invalid_address"
    r1 = t.post("/api/tenant/router/firewall", {"name": "ssh admins", "protocol": "tcp", "dst_ports": "22",
                                                "sources": [{"type": "ipset", "value": s["id"]}],
                                                "destinations": [{"type": "network", "value": env["web"]}]})
    assert r1.status_code == 200, r1.text
    r2 = t.post("/api/tenant/router/firewall", {"name": "web out", "protocol": "tcp_udp", "dst_ports": "80,443,53",
                                                "sources": [{"type": "cidr", "value": "10.20.0.0/24"}]}).json()
    assert t.post("/api/tenant/router/firewall", {"name": "x", "protocol": "icmp", "dst_ports": "1"}).json()[
        "detail"]["code"] == "ports_need_protocol"
    d = t.post("/api/tenant/router/nat", {"name": "ssh in", "kind": "dnat", "external_ip_id": ext, "protocol": "tcp",
                                          "external_port": "2222", "internal_address": "10.20.0.10",
                                          "internal_port": "22"})
    assert d.status_code == 200, d.text
    sn = t.post("/api/tenant/router/nat", {"name": "outbound", "kind": "snat", "external_ip_id": ext,
                                           "internal_address": "10.20.0.0/24"}).json()
    assert sn["external_ip"] == "203.0.113.2"
    res = run_tasks()
    assert [x["kind"] for x in res] == ["router.apply"] and res[0]["status"] == "success"  # changes coalesced
    nft = open(os.path.join(_router_files("198.51.100.10"), "nftables.conf")).read()
    assert "ip saddr { 192.0.2.0/24, 198.51.100.77 } ip daddr { 10.20.0.0/24 } tcp dport { 22 } accept" in nft
    assert "ip daddr 203.0.113.2 tcp dport { 2222 } dnat to 10.20.0.10:22" in nft
    assert 'oifname "wan0" ip saddr 10.20.0.0/24 snat to 203.0.113.2' in nft
    # reorder and stateless
    order = t.post("/api/tenant/router/firewall/order", {"ids": [r2["id"], r1.json()["id"]]}).json()
    assert [x["name"] for x in order] == ["web out", "ssh admins"]
    r = t.patch("/api/tenant/router", {"firewall_stateful": False, "network_ids": [env["web"], env["db"]]})
    assert r.status_code == 200, r.text
    run_tasks()
    nft = open(os.path.join(_router_files("198.51.100.10"), "nftables.conf")).read()
    fwd = nft.split("chain forward")[1].split("chain output")[0]
    assert "ct state established" not in fwd and fwd.index("web out") < fwd.index("ssh admins")
    assert os.path.exists(os.path.join(_router_files("198.51.100.10"), "network", "44-hvd-vl301.network"))
    assert _fake_vm("edgeco__edge")["nics"][2]["allowed"] == "300,301"
    # referential guards
    assert t.delete(f"/api/tenant/networks/{env['web']}").json()["detail"]["code"] == "network_on_router"
    assert t.delete(f"/api/tenant/ip-sets/{s['id']}").json()["detail"]["code"] == "ipset_in_use"
    r = env["prov"].delete(f"/api/provider/external-ips/{ext}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ip_in_use_nat"
    env["ext"] = ext


def test_04_audit_role_and_provider_view(env):
    t = env["t"]
    t.post("/api/tenant/users", {"username": "eaudit", "password": "Audit12345!", "roles": [{"role": "audit"}]})
    a = Api(env["app"])
    a.login("eaudit", "Audit12345!", org="edgeco")
    assert a.get("/api/tenant/router").status_code == 200
    assert a.get("/api/tenant/router/firewall").status_code == 200
    assert a.post("/api/tenant/router/firewall", {"name": "x"}).status_code == 403
    prov = env["prov"]
    routers = prov.get("/api/provider/routers").json()
    rt = next(r for r in routers if r["tenant"] == "edgeco")
    assert rt["mgmt_ip"] == "198.51.100.10" and rt["vm"]["hv_name"] == "edgeco__edge"
    secret = prov.get(f"/api/provider/routers/{rt['id']}/secret").json()
    assert len(secret["root_password"]) >= 16
    assert prov.post(f"/api/provider/vms/{rt['vm']['id']}/console").status_code == 200
    # tenants cannot reach the router VM through the VM API
    assert t.get(f"/api/tenant/vms/{rt['vm']['id']}").status_code == 404
    assert t.post(f"/api/tenant/vms/{rt['vm']['id']}/console").status_code == 404


def test_05_delete_router(env):
    t = env["t"]
    assert t.delete("/api/tenant/router").status_code == 200
    res = run_tasks()
    assert res[-1]["kind"] == "router.delete" and res[-1]["status"] == "success"
    assert t.get("/api/tenant/router").json()["router"] is None
    assert t.get("/api/tenant/summary").json()["quotas"]["vms"]["used"] == 0
    assert env["prov"].delete(f"/api/provider/external-ips/{env['ext']}").status_code == 200
