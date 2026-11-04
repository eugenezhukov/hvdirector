"""Configuration of the router OS (Debian 13) rendered by HV Director.

Layout inside the router VM:

    mgmt0  - OOB management, global routing table (SSH from HV Director only)
    wan0   - WAN uplink (access VLAN of the external network)           ┐
    lan0   - trunk carrying the VLANs of connected tenant networks      │ VRF "vrf-tenant"
    vl<N>  - VLAN SVI on lan0 = gateway of tenant network with VLAN N   ┘ (table 100)

Tenant addressing therefore never collides with the management network.
Firewall / NAT: nftables.  Rules are written against IP addresses (not
interface names) because with a VRF the forward hook sees the VRF device as
input interface.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .netutil import format_ports

VRF_NAME = "vrf-tenant"
VRF_TABLE = 100


@dataclass
class WanSpec:
    vlan: int
    addresses: list[str]  # "203.0.113.10/28"
    gateway: str | None


@dataclass
class DhcpSpec:
    """DHCP server on a tenant SVI (systemd-networkd built-in server)."""

    range_start: str
    range_end: str
    lease_time: int = 86400
    dns: list[str] = field(default_factory=list)
    ntp: list[str] = field(default_factory=list)
    domain: str = ""
    static_leases: list[tuple[str, str]] = field(default_factory=list)  # (mac "aa:bb:..", ip)


@dataclass
class LanSpec:
    vlan: int
    address: str  # gateway of the tenant network, "10.0.0.1/24"
    network: str  # "10.0.0.0/24"
    name: str = ""
    dhcp: DhcpSpec | None = None

    @property
    def ifname(self) -> str:
        return f"vl{self.vlan}"


@dataclass
class FwRuleSpec:
    key: str
    name: str
    action: str  # allow | drop | reject
    protocol: str  # any | tcp | udp | tcp_udp | icmp
    src: list[ipaddress.IPv4Network] | None  # None = any
    dst: list[ipaddress.IPv4Network] | None
    src_ports: list[tuple[int, int]] = field(default_factory=list)
    dst_ports: list[tuple[int, int]] = field(default_factory=list)
    log: bool = False


@dataclass
class NatRuleSpec:
    key: str
    name: str
    kind: str  # snat | dnat
    external_ip: str
    internal: str  # SNAT: source network; DNAT: target host
    protocol: str = "any"  # any | tcp | udp | tcp_udp
    external_ports: list[tuple[int, int]] = field(default_factory=list)
    internal_port: int | None = None
    match: str | None = None  # SNAT: destination filter, DNAT: source filter
    log: bool = False


@dataclass
class RouterSpec:
    hostname: str
    mgmt_mac: str
    wan_mac: str
    lan_mac: str
    mgmt_address: str  # "10.255.0.11/24"
    mgmt_gateway: str | None
    mgmt_dns: list[str]
    mgmt_allowed: list[str]  # CIDRs allowed to SSH to mgmt0 ([] = any on mgmt0)
    wan: WanSpec | None
    lans: list[LanSpec]
    stateful: bool = True
    default_action: str = "drop"
    log_default: bool = False
    fw_rules: list[FwRuleSpec] = field(default_factory=list)
    nat_rules: list[NatRuleSpec] = field(default_factory=list)


def mac_colon(mac: str) -> str:
    m = re.sub(r"[^0-9A-Fa-f]", "", mac).lower()
    return ":".join(m[i:i + 2] for i in range(0, 12, 2))


def _comment(text: str) -> str:
    clean = re.sub(r"[^\w .:,()/+\-]", "_", text, flags=re.UNICODE)[:100]
    return f'comment "{clean}"'


def _set(nets: list[ipaddress.IPv4Network]) -> str:
    return "{ " + ", ".join(str(n) if n.prefixlen < 32 else str(n.network_address) for n in nets) + " }"


# ---------------------------------------------------------------------------
# systemd-networkd
# ---------------------------------------------------------------------------


def render_networkd(spec: RouterSpec) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, mac in (("mgmt0", spec.mgmt_mac), ("wan0", spec.wan_mac), ("lan0", spec.lan_mac)):
        files[f"10-hvd-{name}.link"] = f"[Match]\nMACAddress={mac_colon(mac)}\n\n[Link]\nName={name}\n"

    files["20-hvd-vrf.netdev"] = f"[NetDev]\nName={VRF_NAME}\nKind=vrf\n\n[VRF]\nTable={VRF_TABLE}\n"
    for lan in spec.lans:
        files[f"30-hvd-{lan.ifname}.netdev"] = (
            f"[NetDev]\nName={lan.ifname}\nKind=vlan\n\n[VLAN]\nId={lan.vlan}\n")

    no_v6 = "LinkLocalAddressing=no\nIPv6AcceptRA=no\n"
    mgmt = f"[Match]\nName=mgmt0\n\n[Network]\nAddress={spec.mgmt_address}\n"
    if spec.mgmt_gateway:
        mgmt += f"Gateway={spec.mgmt_gateway}\n"
    for dns in spec.mgmt_dns:
        mgmt += f"DNS={dns}\n"
    mgmt += no_v6
    files["40-hvd-mgmt0.network"] = mgmt

    files["41-hvd-vrf.network"] = f"[Match]\nName={VRF_NAME}\n\n[Network]\n{no_v6}"

    if spec.wan and spec.wan.addresses:
        wan = f"[Match]\nName=wan0\n\n[Network]\nVRF={VRF_NAME}\n"
        for a in spec.wan.addresses:
            wan += f"Address={a}\n"
        wan += no_v6
        if spec.wan.gateway:
            wan += f"\n[Route]\nGateway={spec.wan.gateway}\nTable={VRF_TABLE}\n"
    else:
        wan = f"[Match]\nName=wan0\n\n[Link]\nActivationPolicy=down\n\n[Network]\n{no_v6}"
    files["42-hvd-wan0.network"] = wan

    lan0 = f"[Match]\nName=lan0\n\n[Network]\n"
    for lan in spec.lans:
        lan0 += f"VLAN={lan.ifname}\n"
    lan0 += no_v6
    files["43-hvd-lan0.network"] = lan0

    for lan in spec.lans:
        conf = (f"[Match]\nName={lan.ifname}\n\n[Network]\nVRF={VRF_NAME}\nAddress={lan.address}\n"
                f"ConfigureWithoutCarrier=yes\n{no_v6}")
        if lan.dhcp:
            conf += "DHCPServer=yes\n" + render_dhcp_server(lan)
        files[f"44-hvd-{lan.ifname}.network"] = conf
    return files


def dhcp_pool(network: str, range_start: str, range_end: str) -> tuple[int, int]:
    """networkd pool: offset from the subnet address and number of addresses."""
    net = ipaddress.IPv4Network(network)
    start, end = ipaddress.IPv4Address(range_start), ipaddress.IPv4Address(range_end)
    return int(start) - int(net.network_address), int(end) - int(start) + 1


def render_dhcp_server(lan: LanSpec) -> str:
    """[DHCPServer] and [DHCPServerStaticLease] sections of the SVI .network file.

    The server address (and the default router handed out) is the SVI address,
    i.e. the gateway of the tenant network.  Nothing is propagated from the
    router's own uplinks (UplinkInterface=:none): DNS/NTP are sent only when set.
    """
    d = lan.dhcp
    assert d is not None
    offset, size = dhcp_pool(lan.network, d.range_start, d.range_end)
    out = (f"\n[DHCPServer]\nPoolOffset={offset}\nPoolSize={size}\n"
           f"DefaultLeaseTimeSec={d.lease_time}\nMaxLeaseTimeSec={d.lease_time}\n"
           "UplinkInterface=:none\nEmitRouter=yes\nEmitTimezone=no\n")
    out += ("EmitDNS=yes\nDNS=" + " ".join(d.dns) + "\n") if d.dns else "EmitDNS=no\n"
    out += ("EmitNTP=yes\nNTP=" + " ".join(d.ntp) + "\n") if d.ntp else "EmitNTP=no\n"
    if d.domain:
        out += f"SendOption=15:string:{d.domain}\n"
    for mac, address in d.static_leases:
        out += f"\n[DHCPServerStaticLease]\nMACAddress={mac_colon(mac)}\nAddress={address}\n"
    return out


def render_sysctl() -> str:
    return (
        "# HV Director virtual router\n"
        "net.ipv4.ip_forward = 1\n"
        "net.ipv4.conf.all.rp_filter = 0\n"
        "net.ipv4.conf.default.rp_filter = 0\n"
        "net.ipv4.tcp_l3mdev_accept = 0\n"
        "net.ipv4.udp_l3mdev_accept = 0\n"
        "net.ipv6.conf.all.disable_ipv6 = 1\n"
        "net.ipv6.conf.default.disable_ipv6 = 1\n"
        "net.netfilter.nf_conntrack_max = 262144\n"
    )


# ---------------------------------------------------------------------------
# nftables
# ---------------------------------------------------------------------------


def _l4(protocol: str, src_ports: list[tuple[int, int]], dst_ports: list[tuple[int, int]]) -> list[str]:
    parts: list[str] = []
    if protocol in ("tcp", "udp"):
        if not src_ports and not dst_ports:
            parts.append(f"meta l4proto {protocol}")
        if src_ports:
            parts.append(f"{protocol} sport {{ {format_ports(src_ports)} }}")
        if dst_ports:
            parts.append(f"{protocol} dport {{ {format_ports(dst_ports)} }}")
    elif protocol == "tcp_udp":
        parts.append("meta l4proto { tcp, udp }")
        if src_ports:
            parts.append(f"th sport {{ {format_ports(src_ports)} }}")
        if dst_ports:
            parts.append(f"th dport {{ {format_ports(dst_ports)} }}")
    elif protocol == "icmp":
        parts.append("meta l4proto icmp")
    return parts


def render_fw_rule(r: FwRuleSpec) -> str | None:
    if (r.src is not None and not r.src) or (r.dst is not None and not r.dst):
        return None  # references an empty IP set: never matches
    parts: list[str] = []
    if r.src:
        parts.append(f"ip saddr {_set(r.src)}")
    if r.dst:
        parts.append(f"ip daddr {_set(r.dst)}")
    parts.extend(_l4(r.protocol, r.src_ports, r.dst_ports))
    if r.log:
        parts.append(f'log prefix "hvd-fw-{r.key} " level info')
    if r.action == "allow":
        parts.append("accept")
    elif r.action == "reject":
        parts.append("reject with tcp reset" if r.protocol == "tcp" else "reject with icmpx type admin-prohibited")
    else:
        parts.append("drop")
    parts.append(_comment(r.name))
    return " ".join(parts)


def _nat_proto_variants(protocol: str) -> list[str]:
    return ["tcp", "udp"] if protocol == "tcp_udp" else [protocol]


def render_nat_rule(r: NatRuleSpec) -> list[str]:
    lines: list[str] = []
    for proto in _nat_proto_variants(r.protocol):
        parts: list[str] = []
        if r.kind == "dnat":
            parts.append(f"ip daddr {r.external_ip}")
            if r.match:
                parts.append(f"ip saddr {r.match}")
        else:
            parts.append('oifname "wan0"')
            parts.append(f"ip saddr {r.internal}")
            if r.match:
                parts.append(f"ip daddr {r.match}")
        if proto in ("tcp", "udp"):
            if r.external_ports:
                parts.append(f"{proto} dport {{ {format_ports(r.external_ports)} }}")
            else:
                parts.append(f"meta l4proto {proto}")
        if r.log:
            parts.append(f'log prefix "hvd-nat-{r.key} " level info')
        if r.kind == "dnat":
            target = r.internal
            if r.internal_port and proto in ("tcp", "udp"):
                target += f":{r.internal_port}"
            parts.append(f"dnat to {target}")
        else:
            parts.append(f"snat to {r.external_ip}")
        parts.append(_comment(r.name))
        lines.append(" ".join(parts))
    return lines


def render_nftables(spec: RouterSpec) -> str:
    out: list[str] = ["#!/usr/sbin/nft -f", "# Generated by HV Director - do not edit, changes are overwritten",
                      "flush ruleset", ""]
    mgmt_src = ""
    if spec.mgmt_allowed:
        mgmt_src = "ip saddr { " + ", ".join(spec.mgmt_allowed) + " } "

    out += [
        "table inet hvd_filter {",
        "    chain input {",
        "        type filter hook input priority filter; policy drop;",
        '        iif "lo" accept',
        "        ct state established,related accept",
        "        ct state invalid drop",
        f'        iifname "mgmt0" {mgmt_src}tcp dport 22 accept comment "HV Director management"',
        '        iifname "mgmt0" icmp type echo-request accept',
        '        iifname != "mgmt0" icmp type echo-request limit rate 20/second accept comment "ping router"',
    ]
    dhcp_ifs = [lan.ifname for lan in spec.lans if lan.dhcp]
    if dhcp_ifs:
        # In the VRF the input hook sees the VRF device as iif; sdifname is the SVI the request came from
        names = ", ".join(f'"{i}"' for i in dhcp_ifs)
        out.append(f'        meta sdifname {{ {names} }} udp dport 67 accept comment "DHCP server"')
    out += [
        "    }",
        "",
        "    chain forward {",
        f"        type filter hook forward priority filter; policy {spec.default_action};",
        '        iifname "mgmt0" drop comment "management is out of band"',
        '        oifname "mgmt0" drop',
        "        meta nfproto ipv6 drop",
    ]
    if spec.stateful:
        out += ["        ct state established,related accept", "        ct state invalid drop"]
    else:
        out += ["        icmp type { destination-unreachable, time-exceeded, parameter-problem } accept"]
    for r in spec.fw_rules:
        line = render_fw_rule(r)
        if line is None:
            out.append(f"        # rule {r.key} skipped: empty address set")
            continue
        out.append(f"        {line}")
    if spec.log_default and spec.default_action == "drop":
        out.append('        log prefix "hvd-fw-default " level info')
    out += [
        "    }",
        "",
        "    chain output {",
        "        type filter hook output priority filter; policy accept;",
        "    }",
        "}",
        "",
        "table ip hvd_nat {",
        "    chain prerouting {",
        "        type nat hook prerouting priority dstnat; policy accept;",
    ]
    for r in spec.nat_rules:
        if r.kind == "dnat":
            out += [f"        {line}" for line in render_nat_rule(r)]
    out += [
        "    }",
        "",
        "    chain postrouting {",
        "        type nat hook postrouting priority srcnat; policy accept;",
    ]
    for r in spec.nat_rules:
        if r.kind == "snat":
            out += [f"        {line}" for line in render_nat_rule(r)]
    out += ["    }", "}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Apply script (installed on the router, also re-uploaded on every apply)
# ---------------------------------------------------------------------------

APPLY_SCRIPT = r"""#!/bin/sh
# HV Director router configuration apply script.
# Usage: hvd-apply <staging dir with nftables.conf, sysctl.conf, network/*>
set -eu
S="${1:?staging dir}"
[ -f "$S/nftables.conf" ] || { echo "missing nftables.conf" >&2; exit 2; }
nft -c -f "$S/nftables.conf"

mkdir -p /etc/systemd/network
rm -f /etc/systemd/network/*-hvd-*
cp "$S"/network/* /etc/systemd/network/
install -m 0644 "$S/sysctl.conf" /etc/sysctl.d/90-hvd-router.conf
sysctl -q -p /etc/sysctl.d/90-hvd-router.conf >/dev/null 2>&1 || true

# VLAN SVIs that are no longer configured
for l in $(ip -o link show type vlan | awk -F': ' '{print $2}' | cut -d@ -f1); do
    [ -f "/etc/systemd/network/30-hvd-$l.netdev" ] || ip link del "$l" || true
done

networkctl reload
for l in vrf-tenant wan0 lan0 $(ls /etc/systemd/network | sed -n 's/^30-hvd-\(.*\)\.netdev$/\1/p'); do
    networkctl reconfigure "$l" >/dev/null 2>&1 || true
done

install -m 0644 "$S/nftables.conf" /etc/nftables.conf
nft -f /etc/nftables.conf
echo "HVD-APPLY-OK"
"""


# ---------------------------------------------------------------------------
# DHCP leases: read from systemd-networkd on the router
# ---------------------------------------------------------------------------

_SVI_RE = re.compile(r"^vl\d{1,4}$")
_TEXT_LEASE_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}) \(to ([^)]+)\)")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


def leases_command(ifnames: list[str]) -> str:
    """Shell command printing the DHCP server state of the SVIs (JSON and text form) and the router clocks."""
    names = " ".join(i for i in ifnames if _SVI_RE.match(i))
    return (f"for i in {names}; do "
            'echo "##HVD-IF $i"; echo "##HVD-JSON"; networkctl --no-pager --json=short status "$i" 2>/dev/null; '
            'echo "##HVD-TEXT"; networkctl --no-pager status "$i" 2>/dev/null; '
            "done; "
            'echo "##HVD-CLOCK $(cut -d. -f1 /proc/uptime) $(date +%s)"')


def _hex(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def mac_from_client_id(raw: bytes) -> str | None:
    """MAC address from a DHCP client identifier: type 1 (Ethernet) or IAID+DUID-LL(T) (RFC 4361)."""
    if len(raw) == 7 and raw[0] == 1:
        return _hex(raw[1:])
    if len(raw) > 5 and raw[0] == 255:
        duid = raw[5:]
        if len(duid) >= 4 and int.from_bytes(duid[2:4], "big") == 1:  # hardware type Ethernet
            dtype = int.from_bytes(duid[0:2], "big")
            if dtype == 1 and len(duid) == 14:  # DUID-LLT: type, hwtype, time, MAC
                return _hex(duid[8:14])
            if dtype == 3 and len(duid) == 10:  # DUID-LL: type, hwtype, MAC
                return _hex(duid[4:10])
    return None


def _as_bytes(value: Any) -> bytes | None:
    if isinstance(value, list) and value and all(isinstance(x, int) and 0 <= x <= 255 for x in value):
        return bytes(value)
    if isinstance(value, str):
        h = re.sub(r"[^0-9A-Fa-f]", "", value)
        if h and len(h) % 2 == 0:
            return bytes.fromhex(h)
    return None


def _as_ip(value: Any) -> str | None:
    if isinstance(value, list) and len(value) == 4 and all(isinstance(x, int) for x in value):
        value = ".".join(str(x) for x in value)
    if isinstance(value, str):
        try:
            return str(ipaddress.IPv4Address(value))
        except ValueError:
            return None
    return None


def _remaining(lease: dict[str, Any], uptime: float | None, epoch: float | None) -> int | None:
    for key in ("ExpirationRealtimeUSec", "ExpirationUSec"):
        v = lease.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            secs = v / 1_000_000
            ref = epoch if secs > 1e9 else uptime  # wall clock vs CLOCK_BOOTTIME (= /proc/uptime)
            if ref is not None:
                return max(0, int(secs - ref))
    return None


def _find_dhcp_server(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if isinstance(obj.get("DHCPServer"), dict):
            return obj["DHCPServer"]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = _find_dhcp_server(child)
        if found is not None:
            return found
    return None


def _json_leases(text: str, uptime: float | None, epoch: float | None) -> list[dict[str, Any]] | None:
    docs: list[Any] = []
    for line in text.splitlines():
        if line.strip():
            try:
                docs.append(json.loads(line))
            except ValueError:
                docs = []
                break
    if not docs and text.strip():
        try:
            docs = [json.loads(text)]
        except ValueError:
            return None
    server = next((s for s in (_find_dhcp_server(d) for d in docs) if s is not None), None)
    if server is None:
        return None
    out = []
    for lease in server.get("Leases") or []:
        if not isinstance(lease, dict):
            continue
        address = _as_ip(lease.get("Address"))
        if address is None:
            continue
        raw = _as_bytes(lease.get("ClientId")) or b""
        out.append({"address": address, "mac": mac_from_client_id(raw), "client_id": _hex(raw) if raw else "",
                    "hostname": str(lease.get("Hostname") or ""), "expires_in": _remaining(lease, uptime, epoch)})
    return out


def _text_leases(text: str) -> list[dict[str, Any]]:
    out = []
    for address, client in _TEXT_LEASE_RE.findall(text):
        ip = _as_ip(address)
        if ip is None:
            continue
        client = client.strip()
        mac = client.lower() if _MAC_RE.match(client.lower()) else None
        out.append({"address": ip, "mac": mac, "client_id": client, "hostname": "", "expires_in": None})
    return out


def parse_leases(output: str) -> list[dict[str, Any]]:
    """Active leases from the output of leases_command(): JSON form preferred, text form as a fallback."""
    sections: dict[str, dict[str, str]] = {}
    uptime = epoch = None
    current: str | None = None
    part: str | None = None
    for line in output.splitlines():
        if line.startswith("##HVD-IF "):
            current, part = line.split(None, 1)[1].strip(), None
            sections[current] = {"json": "", "text": ""}
        elif line.strip() in ("##HVD-JSON", "##HVD-TEXT"):
            part = "json" if line.strip() == "##HVD-JSON" else "text"
        elif line.startswith("##HVD-CLOCK"):
            fields = line.split()
            try:
                uptime, epoch = float(fields[1]), float(fields[2])
            except (IndexError, ValueError):
                pass
            current = None
        elif current is not None and part is not None:
            sections[current][part] += line + "\n"
    leases = []
    for ifname, sec in sections.items():
        found = _json_leases(sec["json"], uptime, epoch)
        if found is None:
            found = _text_leases(sec["text"])
        vlan = int(ifname[2:]) if _SVI_RE.match(ifname) else None
        for lease in found:
            leases.append({"interface": ifname, "vlan": vlan, **lease})
    return leases


def render_all(spec: RouterSpec) -> dict[str, str]:
    """Files of the staging directory uploaded to the router."""
    files = {f"network/{k}": v for k, v in render_networkd(spec).items()}
    files["nftables.conf"] = render_nftables(spec)
    files["sysctl.conf"] = render_sysctl()
    files["hvd-apply"] = APPLY_SCRIPT
    return files
