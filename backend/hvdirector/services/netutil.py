"""IPv4 / port parsing and validation helpers for networks, routers, firewall and NAT."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable

from ..errors import AppError

IPv4Net = ipaddress.IPv4Network
IPv4Addr = ipaddress.IPv4Address

MAX_PORT_ITEMS = 50
MAX_ADDRESS_ITEMS = 500


def bad(code: str, message: str, **params: object) -> AppError:
    return AppError(code, message, **params)


def parse_ip(value: str, field: str = "address") -> IPv4Addr:
    try:
        return ipaddress.IPv4Address(str(value).strip())
    except ValueError as exc:
        raise bad("invalid_ip", f"Invalid IPv4 address: {value}", value=str(value), field=field) from exc


def parse_cidr(value: str, field: str = "cidr") -> IPv4Net:
    """Network in CIDR notation; host bits must be zero (192.168.1.0/24)."""
    try:
        net = ipaddress.IPv4Network(str(value).strip(), strict=True)
    except ValueError as exc:
        raise bad("invalid_cidr", f"Invalid IPv4 network: {value}", value=str(value), field=field) from exc
    if net.prefixlen > 30 and field in ("cidr", "network"):
        raise bad("invalid_cidr", f"Network {value} is too small", value=str(value), field=field)
    return net


def parse_interface(address: str, cidr: str) -> tuple[IPv4Addr, IPv4Net]:
    """Validate that an address is a usable host address of a network."""
    net = parse_cidr(cidr)
    ip = parse_ip(address)
    if ip not in net:
        raise bad("ip_not_in_network", f"{ip} is not in {net}", value=str(ip), network=str(net))
    if net.prefixlen < 31 and ip in (net.network_address, net.broadcast_address):
        raise bad("ip_reserved", f"{ip} is the network or broadcast address of {net}", value=str(ip))
    return ip, net


def parse_address_item(value: str) -> list[IPv4Net]:
    """One address item: IP, CIDR (host bits allowed are rejected), or range "a.b.c.d-e.f.g.h"."""
    v = str(value).strip()
    if not v:
        raise bad("invalid_address", "Empty address", value=v)
    if "-" in v:
        lo_s, hi_s = (x.strip() for x in v.split("-", 1))
        lo, hi = parse_ip(lo_s), parse_ip(hi_s)
        if hi < lo:
            raise bad("invalid_range", f"Invalid range {v}", value=v)
        return list(ipaddress.summarize_address_range(lo, hi))
    try:
        return [ipaddress.IPv4Network(v, strict=True)]
    except ValueError as exc:
        raise bad("invalid_address", f"Invalid IPv4 address, network or range: {v}", value=v) from exc


def normalize_addresses(items: Iterable[str]) -> list[IPv4Net]:
    """Parse a list of address items and collapse overlaps (needed for nftables interval sets)."""
    nets: list[IPv4Net] = []
    count = 0
    for item in items:
        count += 1
        if count > MAX_ADDRESS_ITEMS:
            raise bad("too_many_addresses", "Too many address items", max=MAX_ADDRESS_ITEMS)
        nets.extend(parse_address_item(item))
    return list(ipaddress.collapse_addresses(nets))


_PORT_RE = re.compile(r"^(\d{1,5})(?:\s*-\s*(\d{1,5}))?$")


def parse_ports(spec: str | None) -> list[tuple[int, int]]:
    """"80, 443, 8000-8100" -> [(80, 80), (443, 443), (8000, 8100)]; "" -> []."""
    if spec is None or not str(spec).strip():
        return []
    out: list[tuple[int, int]] = []
    normalized = re.sub(r"\s*-\s*", "-", str(spec).strip())
    for part in re.split(r"[,;\s]+", normalized):
        part = part.strip()
        if not part:
            continue
        m = _PORT_RE.match(part)
        if not m:
            raise bad("invalid_port", f"Invalid port or range: {part}", value=part)
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if not (1 <= lo <= 65535 and 1 <= hi <= 65535) or hi < lo:
            raise bad("invalid_port", f"Invalid port or range: {part}", value=part)
        out.append((lo, hi))
        if len(out) > MAX_PORT_ITEMS:
            raise bad("too_many_ports", "Too many ports", max=MAX_PORT_ITEMS)
    return sorted(set(out))


def format_ports(ports: list[tuple[int, int]]) -> str:
    return ", ".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in ports)


def ports_size(ports: list[tuple[int, int]]) -> int:
    return sum(hi - lo + 1 for lo, hi in ports)


def overlaps(a: IPv4Net, b: IPv4Net) -> bool:
    return a.overlaps(b)


def netmask(net: IPv4Net) -> str:
    return str(net.netmask)


def host_addresses(net: IPv4Net) -> Iterable[IPv4Addr]:
    return net.hosts()
