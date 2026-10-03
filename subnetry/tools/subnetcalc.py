"""Subnet / CIDR calculator: everything about an address + prefix, splitting and summarizing.

Accepts "192.168.1.10/24", "192.168.1.10 255.255.255.0", "10.0.0.0/255.0.0.0",
a bare address (treated as a single host) and IPv6 ("2001:db8::1/64").
"""

from __future__ import annotations

import ipaddress
import re

MAX_SUBNETS = 1024  # rows returned when splitting


class SubnetError(ValueError):
    pass


def parse(text: str) -> ipaddress.IPv4Interface | ipaddress.IPv6Interface:
    text = (text or "").strip()
    if not text:
        raise SubnetError("Enter an address with a prefix, e.g. 192.168.1.10/24.")
    parts = re.split(r"[\s/]+", text)
    if len(parts) > 2:
        raise SubnetError("Use the form 192.168.1.10/24 or 192.168.1.10 255.255.255.0.")
    addr, mask = parts[0], (parts[1] if len(parts) == 2 else None)
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError as exc:
        raise SubnetError(f"'{addr}' is not a valid IP address.") from exc
    if mask is None:
        mask = str(ip.max_prefixlen)
    if ip.version == 4 and "." in mask:
        mask = _mask_to_prefix(mask)
    try:
        return ipaddress.ip_interface(f"{ip}/{mask}")
    except ValueError as exc:
        raise SubnetError(f"'{mask}' is not a valid prefix length or netmask.") from exc


def _mask_to_prefix(mask: str) -> str:
    """Netmask (255.255.252.0) or wildcard (0.0.3.255) to a prefix length."""
    try:
        value = int(ipaddress.IPv4Address(mask))
    except ValueError as exc:
        raise SubnetError(f"'{mask}' is not a valid netmask.") from exc
    bits = f"{value:032b}"
    if "01" in bits:  # 0s then 1s: a wildcard mask (as used in Cisco ACLs); invert it
        value ^= 0xFFFFFFFF
        bits = f"{value:032b}"
    if "01" in bits:
        raise SubnetError(f"'{mask}' isn't a valid netmask: the 1-bits must be contiguous.")
    return str(bits.count("1"))


DOCUMENTATION = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]


def address_type(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if any(ip.version == n.version and ip in n for n in DOCUMENTATION):
        return "Documentation / example range (never used on real networks)"
    if ip.is_loopback:
        return "Loopback (this device only)"
    if ip.is_link_local:
        return "Link-local (self-assigned, this network segment only)"
    if ip.is_multicast:
        return "Multicast"
    if ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"):
        return "Carrier-grade NAT (shared address space used by ISPs)"
    if ip.is_private:
        if ip.version == 6 and ip in ipaddress.ip_network("fc00::/7"):
            return "Unique local (private IPv6)"
        if ip.version == 4 and any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
            return "Private (RFC 1918, not routed on the internet)"
        return "Reserved / special-purpose"
    if ip.is_reserved or ip.is_unspecified:
        return "Reserved / special-purpose"
    return "Public (globally routable)"


def ipv4_class(ip: ipaddress.IPv4Address) -> str:
    first = int(ip) >> 24
    if first < 128:
        return "A"
    if first < 192:
        return "B"
    if first < 224:
        return "C"
    if first < 240:
        return "D (multicast)"
    return "E (experimental)"


def _bits(value: int, width: int, group: int) -> str:
    s = f"{value:0{width}b}"
    return ".".join(s[i:i + group] for i in range(0, width, group)) if width == 32 else \
        ":".join(s[i:i + group] for i in range(0, width, group))


def calculate(text: str) -> dict:
    iface = parse(text)
    net, ip = iface.network, iface.ip
    v4 = ip.version == 4
    total = net.num_addresses
    if v4:
        if net.prefixlen == 32:
            first = last = net.network_address
            usable = 1
        elif net.prefixlen == 31:  # point-to-point links: both addresses usable (RFC 3021)
            first, last, usable = net.network_address, net.broadcast_address, 2
        else:
            first, last, usable = net.network_address + 1, net.broadcast_address - 1, total - 2
    else:  # IPv6 has no broadcast; every address is usable
        first, last, usable = net.network_address, net.broadcast_address, total
    width = 32 if v4 else 128
    result = {
        "input": text.strip(),
        "version": ip.version,
        "address": str(ip),
        "cidr": str(net),
        "prefix": net.prefixlen,
        "network": str(net.network_address),
        "netmask": str(net.netmask),
        "wildcard": str(net.hostmask),
        "broadcast": str(net.broadcast_address) if v4 and net.prefixlen < 31 else None,
        "first_usable": str(first),
        "last_usable": str(last),
        "total_addresses": total,
        "usable_hosts": usable,
        "type": address_type(ip),
        "class": ipv4_class(ip) if v4 else None,
        "reverse_pointer": ip.reverse_pointer,
        "is_network_address": v4 and net.prefixlen < 31 and ip == net.network_address,
        "is_broadcast_address": v4 and net.prefixlen < 31 and ip == net.broadcast_address,
        "position": int(ip) - int(net.network_address),
    }
    if v4:
        result["binary"] = {
            "address": _bits(int(ip), 32, 8),
            "netmask": _bits(int(net.netmask), 32, 8),
            "network_bits": net.prefixlen,
        }
        result["hex"] = f"0x{int(ip):08X}"
        result["integer"] = int(ip)
    else:
        result["compressed"] = ip.compressed
        result["exploded"] = ip.exploded
    return result


def split(text: str, new_prefix: int) -> dict:
    """Divide a network into equal smaller subnets."""
    net = parse(text).network
    if not net.prefixlen <= new_prefix <= net.max_prefixlen:
        raise SubnetError(f"The new prefix must be between /{net.prefixlen} and /{net.max_prefixlen}.")
    count = 2 ** (new_prefix - net.prefixlen)
    subnets = []
    for i, sub in enumerate(net.subnets(new_prefix=new_prefix)):
        if i >= MAX_SUBNETS:
            break
        info = calculate(str(sub))
        subnets.append({k: info[k] for k in ("cidr", "network", "first_usable", "last_usable", "broadcast", "usable_hosts")})
    return {"parent": str(net), "new_prefix": new_prefix, "count": count, "shown": len(subnets), "subnets": subnets}


def summarize(lines: str) -> dict:
    """Collapse a list of networks/addresses into the fewest covering CIDR blocks."""
    nets4, nets6 = [], []
    for raw in re.split(r"[\s,;]+", lines or ""):
        if not raw:
            continue
        n = parse(raw).network
        (nets4 if n.version == 4 else nets6).append(n)
    if not nets4 and not nets6:
        raise SubnetError("Enter two or more networks, e.g. 192.168.0.0/24 and 192.168.1.0/24.")
    collapsed = [str(n) for n in ipaddress.collapse_addresses(nets4)] + [str(n) for n in ipaddress.collapse_addresses(nets6)]
    supernet = None
    if len(nets4) > 1 and not nets6:
        lo = min(int(n.network_address) for n in nets4)
        hi = max(int(n.broadcast_address) for n in nets4)
        prefix = 32 - (lo ^ hi).bit_length()
        supernet = str(ipaddress.ip_network(f"{ipaddress.IPv4Address(lo)}/{prefix}", strict=False))
    return {"inputs": [str(n) for n in nets4 + nets6], "collapsed": collapsed, "supernet": supernet}
