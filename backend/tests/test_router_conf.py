"""Router configuration rendering, validation and (when possible) a functional nftables test."""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest

from hvdirector.errors import AppError
from hvdirector.services.netutil import (format_ports, normalize_addresses, parse_address_item, parse_interface,
                                         parse_ports)
from hvdirector.services.routerconf import (FwRuleSpec, LanSpec, NatRuleSpec, RouterSpec, WanSpec, render_all,
                                            render_networkd, render_nftables)

N = ipaddress.IPv4Network


def test_ports():
    assert parse_ports("80, 443,8000 - 8100;22") == [(22, 22), (80, 80), (443, 443), (8000, 8100)]
    assert parse_ports("") == []
    assert format_ports([(22, 22), (8000, 8100)]) == "22, 8000-8100"
    for bad in ("0", "70000", "100-50", "abc", "80;;x"):
        with pytest.raises(AppError):
            parse_ports(bad)


def test_addresses():
    assert parse_address_item("10.0.0.0/24") == [N("10.0.0.0/24")]
    assert parse_address_item("192.0.2.1") == [N("192.0.2.1/32")]
    assert parse_address_item("10.0.0.1-10.0.0.6") == [N("10.0.0.1/32"), N("10.0.0.2/31"), N("10.0.0.4/31"),
                                                       N("10.0.0.6/32")]
    # overlaps are collapsed (nftables interval sets reject overlapping elements)
    assert normalize_addresses(["10.0.0.0/24", "10.0.0.5", "10.0.1.0/24"]) == [N("10.0.0.0/23")]
    with pytest.raises(AppError):
        parse_address_item("10.0.0.1/24")  # host bits set
    with pytest.raises(AppError):
        parse_address_item("10.0.0.9-10.0.0.1")
    ip, net = parse_interface("10.0.0.1", "10.0.0.0/24")
    assert str(ip) == "10.0.0.1" and str(net) == "10.0.0.0/24"
    with pytest.raises(AppError):
        parse_interface("10.0.0.0", "10.0.0.0/24")
    with pytest.raises(AppError):
        parse_interface("10.0.1.1", "10.0.0.0/24")


def _spec(stateful: bool = True, extra_rules: list[FwRuleSpec] | None = None) -> RouterSpec:
    rules = [
        FwRuleSpec(key="r1", name="lan to web", action="allow", protocol="tcp", src=[N("10.10.1.0/24")], dst=None,
                   dst_ports=[(9000, 9000)]),
        FwRuleSpec(key="r2", name="publish web", action="allow", protocol="tcp", src=None,
                   dst=[N("10.10.1.10/32")], dst_ports=[(80, 80)], log=True),
        FwRuleSpec(key="r3", name="dns", action="allow", protocol="tcp_udp", src=None, dst=None,
                   dst_ports=[(53, 53)]),
        FwRuleSpec(key="r4", name="empty set", action="allow", protocol="any", src=[], dst=None),
        FwRuleSpec(key="r5", name="Запрет icmp", action="reject", protocol="icmp", src=None, dst=None),
    ] + (extra_rules or [])
    return RouterSpec(
        hostname="acme-edge", mgmt_mac="00155D000001", wan_mac="00155D000002", lan_mac="00155D000003",
        mgmt_address="198.51.100.2/24", mgmt_gateway="198.51.100.1", mgmt_dns=["198.51.100.1"],
        mgmt_allowed=["198.51.100.0/24"],
        wan=WanSpec(vlan=50, addresses=["203.0.113.2/24", "203.0.113.5/24"], gateway="203.0.113.1"),
        lans=[LanSpec(vlan=100, address="10.10.1.1/24", network="10.10.1.0/24")],
        stateful=stateful, default_action="drop", log_default=True, fw_rules=rules,
        nat_rules=[
            NatRuleSpec(key="n1", name="outbound", kind="snat", external_ip="203.0.113.5", internal="10.10.1.0/24"),
            NatRuleSpec(key="n2", name="web", kind="dnat", external_ip="203.0.113.5", internal="10.10.1.10",
                        protocol="tcp", external_ports=[(8080, 8080)], internal_port=80),
            NatRuleSpec(key="n3", name="dns udp+tcp", kind="dnat", external_ip="203.0.113.5", internal="10.10.1.10",
                        protocol="tcp_udp", external_ports=[(53, 53)], match="192.0.2.0/24"),
        ],
    )


def test_render_networkd_vrf_layout():
    files = render_networkd(_spec())
    assert files["10-hvd-mgmt0.link"].endswith("MACAddress=00:15:5d:00:00:01\n\n[Link]\nName=mgmt0\n")
    assert "Kind=vrf" in files["20-hvd-vrf.netdev"] and "Table=100" in files["20-hvd-vrf.netdev"]
    assert "Id=100" in files["30-hvd-vl100.netdev"]
    mgmt = files["40-hvd-mgmt0.network"]
    assert "VRF=" not in mgmt and "Gateway=198.51.100.1" in mgmt  # management stays in the global table
    wan = files["42-hvd-wan0.network"]
    assert "VRF=vrf-tenant" in wan and "Address=203.0.113.5/24" in wan and "Table=100" in wan
    assert "VLAN=vl100" in files["43-hvd-lan0.network"]
    assert "VRF=vrf-tenant" in files["44-hvd-vl100.network"] and "Address=10.10.1.1/24" in files["44-hvd-vl100.network"]


def test_render_nftables_semantics():
    text = render_nftables(_spec())
    assert "ct state established,related accept" in text.split("chain forward")[1]
    assert 'ip saddr { 10.10.1.0/24 } tcp dport { 9000 } accept comment "lan to web"' in text
    assert 'log prefix "hvd-fw-r2 "' in text
    assert "meta l4proto { tcp, udp } th dport { 53 } accept" in text
    assert "# rule r4 skipped" in text
    assert "reject with icmpx type admin-prohibited" in text
    assert 'oifname "wan0" ip saddr 10.10.1.0/24 snat to 203.0.113.5' in text
    assert "ip daddr 203.0.113.5 tcp dport { 8080 } dnat to 10.10.1.10:80" in text
    assert text.count("ip saddr 192.0.2.0/24") == 2  # tcp_udp DNAT -> two rules
    stateless = render_nftables(_spec(stateful=False))
    assert "ct state established" not in stateless.split("chain forward")[1].split("chain output")[0]
    files = render_all(_spec())
    assert "hvd-apply" in files and "network/42-hvd-wan0.network" in files


def _can_netns() -> bool:
    if os.geteuid() != 0 or not shutil.which("ip") or not shutil.which("nft") or not shutil.which("unshare"):
        return False
    r = subprocess.run(["unshare", "-n", "true"], capture_output=True)
    return r.returncode == 0


needs_netns = pytest.mark.skipif(not _can_netns(), reason="needs root, iproute2, nftables and network namespaces")


@needs_netns
def test_nftables_syntax_check():
    for stateful in (True, False):
        with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as fh:
            fh.write(render_nftables(_spec(stateful)))
        r = subprocess.run(["unshare", "-n", "nft", "-c", "-f", fh.name], capture_output=True, text=True)
        os.unlink(fh.name)
        assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------------------------
# Functional test: real traffic through the generated ruleset in network namespaces.
#   lan ns (10.10.1.10) -- vl100 [router ns] wan0 -- wan ns (203.0.113.1, "internet")
#                                    mgmt0 -- mgmt ns (198.51.100.1)
# VLAN/VRF devices are not needed for the nftables semantics (rules match addresses).
# --------------------------------------------------------------------------------------------

NS = {"lan": "hvdt-lan", "r": "hvdt-r", "wan": "hvdt-wan", "mgmt": "hvdt-mgmt"}

SERVER = textwrap.dedent("""
    import socket, sys
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", int(sys.argv[1]))); s.listen(8)
    while True:
        c, a = s.accept(); c.sendall(a[0].encode()); c.close()
""")

CLIENT = textwrap.dedent("""
    import socket, sys
    try:
        c = socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)
        print(c.recv(100).decode()); c.close()
    except OSError as e:
        print("FAIL", e)
""")


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, check=check)


def nsx(ns: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return sh("ip", "netns", "exec", NS[ns], *args, check=check)


@pytest.fixture()
def topology(tmp_path):
    for ns in NS.values():
        sh("ip", "netns", "del", ns, check=False)
        sh("ip", "netns", "add", ns)
    links = [("lan", "lanc", "vl100"), ("wan", "wanc", "wan0"), ("mgmt", "mgmtc", "mgmt0")]
    for ns, peer, rif in links:
        sh("ip", "link", "add", peer, "netns", NS[ns], "type", "veth", "peer", "name", rif, "netns", NS["r"])
    cfg = [
        ("lan", "ip addr add 10.10.1.10/24 dev lanc", "ip link set lanc up", "ip link set lo up",
         "ip route add default via 10.10.1.1"),
        ("wan", "ip addr add 203.0.113.1/24 dev wanc", "ip link set wanc up", "ip link set lo up"),
        ("mgmt", "ip addr add 198.51.100.1/24 dev mgmtc", "ip link set mgmtc up", "ip link set lo up",
         "ip route add 10.10.1.0/24 via 198.51.100.2"),
        ("r", "ip addr add 10.10.1.1/24 dev vl100", "ip addr add 203.0.113.2/24 dev wan0",
         "ip addr add 203.0.113.5/24 dev wan0", "ip addr add 198.51.100.2/24 dev mgmt0",
         "ip link set vl100 up", "ip link set wan0 up", "ip link set mgmt0 up", "ip link set lo up",
         "sysctl -q -w net.ipv4.ip_forward=1"),
    ]
    for ns, *cmds in cfg:
        for c in cmds:
            nsx(ns, *c.split())
    (tmp_path / "server.py").write_text(SERVER)
    (tmp_path / "client.py").write_text(CLIENT)
    procs = [
        subprocess.Popen(["ip", "netns", "exec", NS["wan"], sys.executable, str(tmp_path / "server.py"), p])
        for p in ("9000", "9001")
    ] + [subprocess.Popen(["ip", "netns", "exec", NS["lan"], sys.executable, str(tmp_path / "server.py"), "80"])]
    time.sleep(0.5)

    def load(spec: RouterSpec) -> None:
        f = tmp_path / "rules.nft"
        f.write_text(render_nftables(spec))
        r = nsx("r", "nft", "-f", str(f), check=False)
        assert r.returncode == 0, r.stderr
        nsx("r", "conntrack", "-F", check=False)

    def connect(ns: str, host: str, port: int) -> str:
        return nsx(ns, sys.executable, str(tmp_path / "client.py"), host, str(port)).stdout.strip()

    yield load, connect
    for p in procs:
        p.kill()
    for ns in NS.values():
        sh("ip", "netns", "del", ns, check=False)


@needs_netns
def test_router_traffic(topology):
    load, connect = topology
    load(_spec(stateful=True))
    # SNAT: the internet server sees the tenant external IP
    assert connect("lan", "203.0.113.1", 9000) == "203.0.113.5"
    # not allowed by any rule -> default drop
    assert connect("lan", "203.0.113.1", 9001).startswith("FAIL")
    # DNAT 203.0.113.5:8080 -> 10.10.1.10:80, allowed by the "publish web" rule
    assert connect("wan", "203.0.113.5", 8080) == "203.0.113.1"
    # management network is out of band: no forwarding from mgmt0 into tenant networks
    assert connect("mgmt", "10.10.1.10", 80).startswith("FAIL")

    # Stateless: replies are not allowed automatically ...
    load(_spec(stateful=False))
    assert connect("lan", "203.0.113.1", 9000).startswith("FAIL")
    # ... until an explicit rule allows the return direction
    reply = FwRuleSpec(key="r9", name="replies", action="allow", protocol="tcp", src=None,
                       dst=[N("10.10.1.0/24")], src_ports=[(9000, 9000)])
    load(_spec(stateful=False, extra_rules=[reply]))
    assert connect("lan", "203.0.113.1", 9000) == "203.0.113.5"


# --------------------------------------------------------------------------------------------
# Installer ISO remastering (xorriso) and preseed validity (debconf-set-selections -c)
# --------------------------------------------------------------------------------------------

def _install_spec():
    from hvdirector.services.routerprov import InstallSpec, generate_ed25519

    host_priv, host_pub = generate_ed25519()
    _, client_pub = generate_ed25519("hvdirector")
    return InstallSpec(hostname="acme-edge", mgmt_ip="198.51.100.2", mgmt_prefix=24, mgmt_gateway="198.51.100.1",
                       dns=["198.51.100.1", "198.51.100.53"], root_password="Abcdef123456", authorized_keys=[client_pub],
                       host_key_private=host_priv, host_key_public=host_pub, mirror_host="mirror.example.org",
                       http_proxy="http://proxy.example.org:3128")


def test_preseed_and_grub():
    from hvdirector.services.routerprov import render_grub_cfg, render_preseed

    s = _install_spec()
    pre = render_preseed(s)
    assert "d-i netcfg/get_ipaddress string 198.51.100.2" in pre
    assert "d-i netcfg/get_netmask string 255.255.255.0" in pre
    assert "d-i netcfg/get_nameservers string 198.51.100.1 198.51.100.53" in pre
    assert "d-i mirror/http/hostname string mirror.example.org" in pre
    assert "nftables openssh-server hyperv-daemons" in pre
    assert "d-i preseed/late_command string sh /cdrom/hvd/late.sh" in pre
    grub = render_grub_cfg(s)
    assert "preseed/file=/cdrom/preseed.cfg" in grub and "netcfg/get_ipaddress=198.51.100.2" in grub
    offline = render_preseed(_install_spec().__class__(**{**s.__dict__, "mirror_host": ""}))
    assert "d-i apt-setup/use_mirror boolean false" in offline
    if shutil.which("debconf-set-selections"):
        with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as fh:
            fh.write(pre)
        r = subprocess.run(["debconf-set-selections", "-c", fh.name], capture_output=True, text=True)
        os.unlink(fh.name)
        assert r.returncode == 0, r.stderr
    with pytest.raises(AppError):
        render_preseed(_install_spec().__class__(**{**s.__dict__, "hostname": "bad host"}))


@pytest.mark.skipif(not shutil.which("xorriso"), reason="xorriso not installed")
def test_remaster_iso(tmp_path):
    from hvdirector.services.routerprov import extract_file, installer_files, remaster_iso

    tree = tmp_path / "tree"
    (tree / "boot" / "grub").mkdir(parents=True)
    (tree / "install.amd").mkdir()
    (tree / "boot" / "grub" / "grub.cfg").write_text("menuentry 'Graphical install' {}\n")
    (tree / "install.amd" / "vmlinuz").write_bytes(b"kernel")
    (tree / "install.amd" / "initrd.gz").write_bytes(b"initrd")
    src = tmp_path / "debian-13-netinst.iso"
    subprocess.run(["xorriso", "-as", "mkisofs", "-o", str(src), "-V", "Debian 13", "-R", "-J", str(tree)],
                   check=True, capture_output=True)
    out = tmp_path / "router.iso"
    files = installer_files(_spec(), _install_spec())
    remaster_iso(str(src), str(out), files)
    assert "auto=true" in extract_file(str(out), "/boot/grub/grub.cfg")
    assert "d-i passwd/root-password password Abcdef123456" in extract_file(str(out), "/preseed.cfg")
    assert "in-target systemctl enable systemd-networkd.service" in extract_file(str(out), "/hvd/late.sh")
    assert "Kind=vrf" in extract_file(str(out), "/hvd/stage/network/20-hvd-vrf.netdev")
    assert "dnat to 10.10.1.10:80" in extract_file(str(out), "/hvd/stage/nftables.conf")
    assert extract_file(str(out), "/install.amd/vmlinuz") == "kernel"  # original content preserved
