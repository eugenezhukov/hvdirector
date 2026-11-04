"""DHCP server of virtual routers: rendering, lease parsing, validation and a live systemd-networkd test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import types
import uuid

import pytest

from hvdirector.errors import AppError
from hvdirector.services import routers as rsvc
from hvdirector.services.routerconf import (DhcpSpec, LanSpec, RouterSpec, dhcp_pool, leases_command,
                                            mac_from_client_id, parse_leases, render_networkd, render_nftables)

DHCP = DhcpSpec(range_start="192.168.100.100", range_end="192.168.100.199", lease_time=3600,
                dns=["192.168.100.53", "1.1.1.1"], ntp=["192.168.100.123"], domain="corp.example",
                static_leases=[("00155D0A0B0C", "192.168.100.20")])


def _lan(dhcp: DhcpSpec | None, vlan: int = 100) -> LanSpec:
    third = vlan % 256
    return LanSpec(vlan=vlan, address=f"192.168.{third}.1/24", network=f"192.168.{third}.0/24", name=f"n{vlan}",
                   dhcp=dhcp)


def _spec(*lans: LanSpec) -> RouterSpec:
    return RouterSpec(hostname="t-edge", mgmt_mac="00155D000001", wan_mac="00155D000002", lan_mac="00155D000003",
                      mgmt_address="198.51.100.2/24", mgmt_gateway="198.51.100.1", mgmt_dns=["198.51.100.1"],
                      mgmt_allowed=[], wan=None, lans=list(lans))


# ---------------------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------------------


def test_render_dhcp_server():
    conf = render_networkd(_spec(_lan(DHCP)))["44-hvd-vl100.network"]
    network, server = conf.split("[DHCPServer]")
    assert "VRF=vrf-tenant" in network and "Address=192.168.100.1/24" in network and "DHCPServer=yes" in network
    for line in ("PoolOffset=100", "PoolSize=100", "DefaultLeaseTimeSec=3600", "MaxLeaseTimeSec=3600",
                 "UplinkInterface=:none", "EmitRouter=yes", "EmitTimezone=no", "EmitDNS=yes",
                 "DNS=192.168.100.53 1.1.1.1", "EmitNTP=yes", "NTP=192.168.100.123",
                 "SendOption=15:string:corp.example"):
        assert f"\n{line}\n" in server, line
    assert server.endswith("[DHCPServerStaticLease]\nMACAddress=00:15:5d:0a:0b:0c\nAddress=192.168.100.20\n")
    # nothing of the router itself (management DNS, timezone) leaks to the tenant clients
    minimal = render_networkd(_spec(_lan(DhcpSpec(range_start="192.168.100.10", range_end="192.168.100.10"))))
    text = minimal["44-hvd-vl100.network"]
    assert "PoolOffset=10\nPoolSize=1\n" in text and "EmitDNS=no" in text and "EmitNTP=no" in text
    assert "198.51.100.1" not in text and "SendOption" not in text and "StaticLease" not in text
    assert "DHCPServer" not in render_networkd(_spec(_lan(None)))["44-hvd-vl100.network"]
    assert dhcp_pool("10.0.0.0/16", "10.0.1.0", "10.0.2.255") == (256, 512)


def test_dhcp_firewall_rule():
    text = render_nftables(_spec(_lan(DHCP, 100), _lan(None, 101),
                                 _lan(DhcpSpec(range_start="192.168.102.10", range_end="192.168.102.20"), 102)))
    inp = text.split("chain input")[1].split("chain forward")[0]
    # in the VRF the input hook sees the VRF device; the SVI the request came from is the slave device
    assert 'meta sdifname { "vl100", "vl102" } udp dport 67 accept comment "DHCP server"' in inp
    assert "dport 67" not in render_nftables(_spec(_lan(None)))


def _can_netns() -> bool:
    if os.geteuid() != 0 or not shutil.which("nft") or not shutil.which("unshare"):
        return False
    return subprocess.run(["unshare", "-n", "true"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _can_netns(), reason="needs root, nftables and network namespaces")
def test_dhcp_nftables_syntax():
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as fh:
        fh.write(render_nftables(_spec(_lan(DHCP, 100), _lan(DHCP, 101))))
    r = subprocess.run(["unshare", "-n", "nft", "-c", "-f", fh.name], capture_output=True, text=True)
    os.unlink(fh.name)
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------------------------------------------
# Leases (output format of systemd-networkd, captured from a real networkd)
# ---------------------------------------------------------------------------------------------------------------

NETWORKD_JSON = {
    "Index": 3, "Name": "vl100", "OperationalState": "routable",
    "DHCPServer": {
        "PoolOffset": 100, "PoolSize": 100,
        "Leases": [
            {"ClientId": [1, 0, 21, 93, 10, 11, 12], "Address": [192, 168, 100, 20], "Hostname": "web-1",
             "ExpirationUSec": 7187828461},
            {"ClientId": [1, 0, 21, 93, 153, 0, 1], "Address": [192, 168, 100, 101], "Hostname": "web-2",
             "ExpirationUSec": 7187863090},
            {"ClientId": [255, 0, 0, 0, 1, 0, 2, 0, 0, 171, 17, 0, 1, 2, 3, 4, 5, 6, 7],
             "Address": [192, 168, 100, 102], "ExpirationUSec": 7187894629},
        ],
        "StaticLeases": [{"ClientId": [1, 0, 21, 93, 10, 11, 12], "Address": [192, 168, 100, 20]}],
    },
}

NETWORKD_TEXT = """● 3: vl100
            Network File: /etc/systemd/network/44-hvd-vl100.network
                   State: routable (configured)
     Offered DHCP leases: 192.168.100.20 (to 00:15:5d:0a:0b:0c)
                          192.168.100.102 (to IAID:0x1/DUID)
"""


def test_parse_leases_json_and_text():
    out = (f"##HVD-IF vl100\n##HVD-JSON\n{json.dumps(NETWORKD_JSON)}\n##HVD-TEXT\n{NETWORKD_TEXT}"
           "##HVD-IF vl101\n##HVD-JSON\n##HVD-TEXT\n"
           "##HVD-CLOCK 3587 1793819854\n")
    leases = parse_leases(out)
    assert [(x["vlan"], x["address"], x["mac"], x["hostname"]) for x in leases] == [
        (100, "192.168.100.20", "00:15:5d:0a:0b:0c", "web-1"),
        (100, "192.168.100.101", "00:15:5d:99:00:01", "web-2"),
        (100, "192.168.100.102", None, ""),
    ]
    # ExpirationUSec is CLOCK_BOOTTIME: remaining time = expiration - /proc/uptime of the router
    assert [x["expires_in"] for x in leases] == [3600, 3600, 3600]
    assert leases[2]["client_id"].startswith("ff:00:00:00:01")
    # networkctl without JSON support / D-Bus describe: text form
    text_only = parse_leases(f"##HVD-IF vl100\n##HVD-JSON\n##HVD-TEXT\n{NETWORKD_TEXT}##HVD-CLOCK 1 2\n")
    assert [(x["address"], x["mac"], x["client_id"], x["expires_in"]) for x in text_only] == [
        ("192.168.100.20", "00:15:5d:0a:0b:0c", "00:15:5d:0a:0b:0c", None),
        ("192.168.100.102", None, "IAID:0x1/DUID", None),
    ]
    # wall-clock expiration (newer networkd), string addresses
    lease = {"ClientId": "01:00:15:5d:00:00:09", "Address": "10.0.0.9", "ExpirationRealtimeUSec": 1_800_000_600_000_000}
    doc = json.dumps({"Name": "vl7", "DHCPServer": {"Leases": [lease]}})
    [one] = parse_leases(f"##HVD-IF vl7\n##HVD-JSON\n{doc}\n##HVD-TEXT\n##HVD-CLOCK 50 1800000000\n")
    assert one["vlan"] == 7 and one["mac"] == "00:15:5d:00:00:09" and one["expires_in"] == 600
    assert parse_leases("garbage\n{not json\n") == []
    assert parse_leases("##HVD-IF vl5\n##HVD-JSON\n{\"Name\": \"vl5\"}\n##HVD-TEXT\nnone\n") == []


def test_client_id_mac_and_command():
    assert mac_from_client_id(bytes([1, 0, 0x15, 0x5D, 1, 2, 3])) == "00:15:5d:01:02:03"
    iaid = bytes([255, 0, 0, 0, 1])
    duid_ll = bytes([0, 3, 0, 1, 0, 0x15, 0x5D, 1, 2, 3])
    duid_llt = bytes([0, 1, 0, 1, 0x2A, 0x2B, 0x2C, 0x2D, 0, 0x15, 0x5D, 4, 5, 6])
    assert mac_from_client_id(iaid + duid_ll) == "00:15:5d:01:02:03"
    assert mac_from_client_id(iaid + duid_llt) == "00:15:5d:04:05:06"
    assert mac_from_client_id(iaid + bytes([0, 2, 0, 0, 0xAB, 0x11, 1, 2])) is None  # DUID-EN has no MAC
    cmd = leases_command(["vl100", "vl4094", "vl1;reboot", "eth0"])
    assert cmd.startswith("for i in vl100 vl4094; do ") and "reboot" not in cmd and "eth0" not in cmd


# ---------------------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------------------


def _binding(name: str, mac: str, address: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=uuid.uuid4(), name=name, mac=mac, address=address)


def _net(cidr: str | None = "10.20.0.0/24", gateway: str | None = "10.20.0.1", dhcp=None, bindings=()):  # noqa: ANN001
    return types.SimpleNamespace(name="web", cidr=cidr, gateway=gateway, dhcp=dhcp, dhcp_bindings=list(bindings))


def _code(fn, *args, **kw) -> str:  # noqa: ANN001
    with pytest.raises(AppError) as exc:
        fn(*args, **kw)
    return exc.value.code


def test_validate_dhcp_settings():
    ok = rsvc.validate_dhcp(_net(), {"range_start": " 10.20.0.100", "range_end": "10.20.0.199", "lease_time": 3600,
                                     "dns_servers": ["10.20.0.53", "", "1.1.1.1", "10.20.0.53"],
                                     "domain_name": "Corp.Example."})
    assert ok == {"enabled": True, "range_start": "10.20.0.100", "range_end": "10.20.0.199", "lease_time": 3600,
                  "dns_servers": ["10.20.0.53", "1.1.1.1"], "ntp_servers": [], "domain_name": "corp.example"}
    base = {"range_start": "10.20.0.100", "range_end": "10.20.0.199"}
    assert _code(rsvc.validate_dhcp, _net(), dict(base, range_start="10.20.0.1")) == "dhcp_range_gateway"
    assert _code(rsvc.validate_dhcp, _net(), dict(base, range_start="10.20.0.200")) == "invalid_range"
    assert _code(rsvc.validate_dhcp, _net(), dict(base, range_end="10.20.1.5")) == "ip_not_in_network"
    assert _code(rsvc.validate_dhcp, _net(), dict(base, range_end="10.20.0.255")) == "ip_reserved"
    assert _code(rsvc.validate_dhcp, _net(), dict(base, range_end="x")) == "invalid_ip"
    assert _code(rsvc.validate_dhcp, _net(), dict(base, lease_time=60)) == "invalid_lease_time"
    assert _code(rsvc.validate_dhcp, _net(), dict(base, dns_servers=[f"10.0.0.{i}" for i in range(5)])) == \
        "too_many_servers"
    for bad in ("0.0.0.0", "224.0.0.1", "127.0.0.1", "255.255.255.255", "dns.example"):
        assert _code(rsvc.validate_dhcp, _net(), dict(base, ntp_servers=[bad])) == "invalid_ip", bad
    for bad in ("-corp.example", "corp..example", "corp example", "a" * 64 + ".com", "corp_example"):
        assert _code(rsvc.validate_dhcp, _net(), dict(base, domain_name=bad)) == "invalid_domain_name", bad
    assert _code(rsvc.validate_dhcp, _net(cidr=None, gateway=None), base) == "network_needs_gateway"
    inside = _net(bindings=[_binding("db", "00:15:5d:00:00:01", "10.20.0.150")])
    assert _code(rsvc.validate_dhcp, inside, base) == "dhcp_binding_in_range"


def test_validate_bindings_and_mac():
    assert rsvc.normalize_mac("00155D0A0B0C") == "00:15:5d:0a:0b:0c"
    assert rsvc.normalize_mac("00-15-5D-0A-0B-0C") == "00:15:5d:0a:0b:0c"
    assert rsvc.normalize_mac("0015.5d0a.0b0c") == "00:15:5d:0a:0b:0c"
    for bad in ("", "zz:15:5d:0a:0b:0c", "00:15:5d:0a:0b", "01:00:5e:00:00:01", "ff:ff:ff:ff:ff:ff", "000000000000"):
        assert _code(rsvc.normalize_mac, bad) == "invalid_mac", bad
    dhcp = types.SimpleNamespace(range_start="10.20.0.100", range_end="10.20.0.199")
    existing = _binding("db", "00:15:5d:00:00:01", "10.20.0.20")
    n = _net(dhcp=dhcp, bindings=[existing])
    ok = rsvc.validate_dhcp_binding(n, {"name": " app ", "mac": "00155D000002", "address": "10.20.0.21"})
    assert ok == {"name": "app", "mac": "00:15:5d:00:00:02", "address": "10.20.0.21", "description": ""}
    good = {"name": "app", "mac": "00155D000002", "address": "10.20.0.21"}
    assert _code(rsvc.validate_dhcp_binding, n, dict(good, address="10.20.0.150")) == "dhcp_binding_in_range"
    assert _code(rsvc.validate_dhcp_binding, n, dict(good, address="10.20.0.1")) == "ip_is_gateway"
    assert _code(rsvc.validate_dhcp_binding, n, dict(good, address="10.20.0.0")) == "ip_reserved"
    assert _code(rsvc.validate_dhcp_binding, n, dict(good, mac="00:15:5d:00:00:01")) == "dhcp_binding_mac_exists"
    assert _code(rsvc.validate_dhcp_binding, n, dict(good, address="10.20.0.20")) == "dhcp_binding_ip_exists"
    assert _code(rsvc.validate_dhcp_binding, n, dict(good, name="")) == "invalid_binding_name"
    # editing a binding does not conflict with itself
    same = rsvc.validate_dhcp_binding(n, {"name": "db", "mac": "00155D000001", "address": "10.20.0.20"},
                                      binding_id=existing.id)
    assert same["address"] == "10.20.0.20"
    # a subnet / gateway change must keep the pool and the bindings valid
    n.dhcp = types.SimpleNamespace(enabled=True, range_start="10.20.0.100", range_end="10.20.0.199", lease_time=3600,
                                   domain_name="")
    rsvc.revalidate_network_dhcp(n)
    n.cidr, n.gateway = "10.21.0.0/24", "10.21.0.1"
    assert _code(rsvc.revalidate_network_dhcp, n) == "network_dhcp_conflict"
    n.cidr, n.gateway = "10.20.0.0/24", "10.20.0.150"
    assert _code(rsvc.revalidate_network_dhcp, n) == "network_dhcp_conflict"
    n.dhcp, n.gateway = None, "10.20.0.20"  # bindings only
    assert _code(rsvc.revalidate_network_dhcp, n) == "network_dhcp_conflict"


def test_range_suggestion():
    def s(cidr: str | None, gw: str | None) -> tuple[str, str] | None:
        return rsvc.dhcp_range_suggestion(_net(cidr, gw))

    assert s("10.20.0.0/24", "10.20.0.1") == ("10.20.0.128", "10.20.0.254")
    assert s("10.20.0.0/24", "10.20.0.254") == ("10.20.0.128", "10.20.0.253")
    assert s("10.20.0.0/24", "10.20.0.200") == ("10.20.0.201", "10.20.0.254")
    assert s("10.20.0.0/29", "10.20.0.6") == ("10.20.0.4", "10.20.0.5")
    assert s("10.20.0.0/30", "10.20.0.1") == ("10.20.0.2", "10.20.0.2")
    assert s(None, None) is None


# ---------------------------------------------------------------------------------------------------------------
# Live test: the rendered .network file served by a real systemd-networkd to DHCP clients.
# A private mount + network namespace gets its own /run, sysfs and D-Bus; the client runs in a nested netns.
# The test kernel may lack VLAN/VRF support, so the SVI is a veth end and the VRF= line is dropped; for the same
# reason "meta sdifname" (which matches the SVI inside the VRF) is replaced by "iifname" when loading nftables.
# ---------------------------------------------------------------------------------------------------------------

NETWORKD = next((p for p in ("/usr/lib/systemd/systemd-networkd", "/lib/systemd/systemd-networkd")
                 if os.path.exists(p)), None)

DHCP_CLIENT = textwrap.dedent('''
    import json, random, socket, struct, sys
    IF, KIND = sys.argv[1], sys.argv[2]
    hw = bytes.fromhex(open(f"/sys/class/net/{IF}/address").read().strip().replace(":", ""))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, IF.encode())
    s.bind(("0.0.0.0", 68))
    s.settimeout(8)
    xid = random.getrandbits(32)
    # client identifier: type 1 + MAC (Windows, dhclient) or IAID + DUID-EN (systemd-networkd clients)
    cid = bytes([1]) + hw if KIND == "mac" else bytes([255, 0, 0, 0, 1, 0, 2, 0, 0, 0xAB, 0x11]) + bytes(range(8))
    def packet(msgtype, opts):
        p = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, xid, 0, 0x8000, bytes(4), bytes(4), bytes(4),
                        bytes(4), hw.ljust(16, bytes(1)), b"", b"")
        p += bytes([99, 130, 83, 99, 53, 1, msgtype, 61, len(cid)]) + cid + bytes([12, 6]) + b"testvm"
        for code, val in opts:
            p += bytes([code, len(val)]) + val
        return p + bytes([55, 6, 1, 3, 6, 15, 42, 51, 255])
    def receive(expect):
        while True:
            data, _ = s.recvfrom(4096)
            if len(data) < 240 or struct.unpack("!I", data[4:8])[0] != xid:
                continue
            opts, i = {}, 240
            while i < len(data) and data[i] != 255:
                if data[i] == 0:
                    i += 1
                    continue
                opts[data[i]] = data[i + 2:i + 2 + data[i + 1]]
                i += 2 + data[i + 1]
            if opts.get(53, b"\\0")[0] == expect:
                return socket.inet_ntoa(data[16:20]), opts
    ips = lambda b: [socket.inet_ntoa(b[i:i + 4]) for i in range(0, len(b or b""), 4)]
    s.sendto(packet(1, []), ("255.255.255.255", 67))
    ip, offer = receive(2)
    s.sendto(packet(3, [(50, socket.inet_aton(ip)), (54, offer[54])]), ("255.255.255.255", 67))
    ip, ack = receive(5)
    print(json.dumps({"ip": ip, "router": ips(ack.get(3)), "mask": ips(ack.get(1)), "dns": ips(ack.get(6)),
                      "ntp": ips(ack.get(42)), "domain": (ack.get(15) or b"").decode(),
                      "lease": struct.unpack("!I", ack[51])[0], "timezone": 100 in ack or 101 in ack}))
''')

LIVE_SCRIPT = textwrap.dedent('''
    set -u
    D="$1"
    mount -t tmpfs tmpfs /run
    mkdir -p /run/dbus /run/netns /run/systemd/netif
    chown systemd-network:systemd-network /run/systemd/netif 2>/dev/null || true
    echo other > /run/systemd/container   # networkd does not wait for udev in a "container"
    mount -t sysfs -o ro sysfs /sys
    mount --bind "$D/network" /etc/systemd/network
    ip link set lo up
    ip link add vl100 type veth peer name cl0
    ip netns add client
    ip link set cl0 netns client
    ip netns exec client ip link set lo up
    ip netns exec client ip link set cl0 up
    command -v dbus-daemon >/dev/null && dbus-daemon --system --fork --nopidfile >/dev/null 2>&1
    "$NETWORKD" >"$D/networkd.log" 2>&1 &
    NPID=$!
    for _ in $(seq 1 40); do ip -4 addr show vl100 | grep -q 192.168.100.1 && break; sleep 0.25; done
    sed "s/meta sdifname/iifname/" "$D/nftables.conf" >/run/rules.nft && nft -f /run/rules.nft || echo "NFT-FAILED"
    client() { ip netns exec client ip link set cl0 address "$1"; ip netns exec client python3 "$D/client.py" cl0 "$2"; }
    echo "##RESULT static"; client 00:15:5d:0a:0b:0c mac
    echo "##RESULT dynamic"; client 00:15:5d:99:00:01 mac
    echo "##RESULT duid"; client 00:15:5d:99:00:02 duid
    echo "##RESULT leases"
    if command -v dbus-daemon >/dev/null; then bash -c "$LEASES_CMD"; fi
    kill "$NPID"
''')


def _can_run_networkd() -> bool:
    return bool(NETWORKD) and _can_netns() and bool(shutil.which("ip")) and bool(shutil.which("python3"))


@pytest.mark.skipif(not _can_run_networkd(), reason="needs root, systemd-networkd, iproute2 and network namespaces")
def test_live_networkd_dhcp_server(tmp_path):
    spec = _spec(_lan(DhcpSpec(range_start="192.168.100.100", range_end="192.168.100.102", lease_time=3600,
                               dns=["192.168.100.53", "1.1.1.1"], ntp=["192.168.100.123"], domain="corp.example",
                               static_leases=[("00155D0A0B0C", "192.168.100.20")])))
    (tmp_path / "network").mkdir()
    conf = render_networkd(spec)["44-hvd-vl100.network"]
    (tmp_path / "network" / "44-hvd-vl100.network").write_text(conf.replace("VRF=vrf-tenant\n", ""))
    (tmp_path / "nftables.conf").write_text(render_nftables(spec))
    (tmp_path / "client.py").write_text(DHCP_CLIENT)
    (tmp_path / "live.sh").write_text(LIVE_SCRIPT)
    env = dict(os.environ, NETWORKD=NETWORKD or "", LEASES_CMD=leases_command(["vl100"]))
    r = subprocess.run(["unshare", "-mn", "--propagation", "private", "bash", str(tmp_path / "live.sh"),
                        str(tmp_path)], capture_output=True, text=True, timeout=120, env=env)
    log = (tmp_path / "networkd.log").read_text() if (tmp_path / "networkd.log").exists() else ""
    assert "NFT-FAILED" not in r.stdout, r.stdout + r.stderr
    parts = dict(p.split("\n", 1) for p in r.stdout.split("##RESULT ")[1:])
    try:
        got = {k: json.loads(parts[k]) for k in ("static", "dynamic", "duid")}
    except (KeyError, ValueError):
        pytest.fail(f"DHCP exchange failed:\n{r.stdout}\n{r.stderr}\n{log}")
    common = {"router": ["192.168.100.1"], "mask": ["255.255.255.0"], "dns": ["192.168.100.53", "1.1.1.1"],
              "ntp": ["192.168.100.123"], "domain": "corp.example", "lease": 3600, "timezone": False}
    assert got["static"] == dict(common, ip="192.168.100.20")
    dynamic = {k: got[k].pop("ip") for k in ("dynamic", "duid")}
    assert got["dynamic"] == common and got["duid"] == common
    assert set(dynamic.values()) <= {"192.168.100.100", "192.168.100.101", "192.168.100.102"}
    assert dynamic["dynamic"] != dynamic["duid"]
    if shutil.which("dbus-daemon"):
        leases = {x["address"]: x for x in parse_leases(parts["leases"])}
        assert set(leases) == {"192.168.100.20", dynamic["dynamic"], dynamic["duid"]}, parts["leases"]
        assert leases["192.168.100.20"]["mac"] == "00:15:5d:0a:0b:0c"
        assert leases[dynamic["dynamic"]]["mac"] == "00:15:5d:99:00:01"
        assert leases[dynamic["duid"]]["mac"] is None  # DUID-EN client: no MAC in the client identifier
        assert all(x["hostname"] == "testvm" and 3500 <= (x["expires_in"] or 0) <= 3600 for x in leases.values())
