"""Public IP information: your internet-facing address, ISP, ASN and approximate location.

Lookups go to ipinfo.io (no account needed for light use) with ipwho.is as a
fallback. Location from IP is approximate: usually the ISP's nearest hub, not your home.
"""

from __future__ import annotations

import asyncio
import ipaddress

import httpx

TIMEOUT = 6.0


class IpInfoError(RuntimeError):
    pass


def validate_public_ip(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError as exc:
        raise IpInfoError(f"'{ip}' is not a valid IP address.") from exc
    if not addr.is_global:
        raise IpInfoError(f"{addr} is a private/reserved address. It only exists inside a local network, "
                          "so it has no public owner or location.")
    return str(addr)


def from_ipinfo(d: dict) -> dict:
    org = d.get("org") or ""
    asn, _, isp = org.partition(" ") if org.startswith("AS") else ("", "", org)
    lat, _, lon = (d.get("loc") or ",").partition(",")
    return {
        "ip": d.get("ip"),
        "hostname": d.get("hostname"),
        "isp": isp or None,
        "asn": asn or None,
        "city": d.get("city"),
        "region": d.get("region"),
        "country": d.get("country"),
        "postal": d.get("postal"),
        "timezone": d.get("timezone"),
        "latitude": float(lat) if lat else None,
        "longitude": float(lon) if lon else None,
        "anycast": bool(d.get("anycast")),
        "source": "ipinfo.io",
    }


def from_ipwhois(d: dict) -> dict:
    conn = d.get("connection") or {}
    return {
        "ip": d.get("ip"),
        "hostname": None,
        "isp": conn.get("isp") or conn.get("org"),
        "asn": f"AS{conn['asn']}" if conn.get("asn") else None,
        "city": d.get("city"),
        "region": d.get("region"),
        "country": d.get("country_code"),
        "postal": d.get("postal"),
        "timezone": (d.get("timezone") or {}).get("id"),
        "latitude": d.get("latitude"),
        "longitude": d.get("longitude"),
        "anycast": False,
        "source": "ipwho.is",
    }


async def _lookup(client: httpx.AsyncClient, ip: str | None) -> dict:
    errors = []
    try:
        r = await client.get(f"https://ipinfo.io/{ip + '/' if ip else ''}json")
        r.raise_for_status()
        data = r.json()
        if not data.get("bogon") and data.get("ip"):
            return from_ipinfo(data)
        errors.append("ipinfo.io returned no data")
    except (httpx.HTTPError, ValueError) as exc:
        errors.append(f"ipinfo.io: {exc}")
    try:
        r = await client.get(f"https://ipwho.is/{ip or ''}")
        r.raise_for_status()
        data = r.json()
        if data.get("success", True) and data.get("ip"):
            return from_ipwhois(data)
        errors.append(f"ipwho.is: {data.get('message', 'no data')}")
    except (httpx.HTTPError, ValueError) as exc:
        errors.append(f"ipwho.is: {exc}")
    raise IpInfoError("Could not look up IP information. " + "; ".join(errors))


async def _public_ipv6(client: httpx.AsyncClient) -> str | None:
    """api64 answers over IPv6 when the connection has it, otherwise returns the IPv4 address."""
    try:
        r = await client.get("https://api64.ipify.org", params={"format": "json"})
        ip = r.json().get("ip", "")
        return ip if ":" in ip else None
    except (httpx.HTTPError, ValueError):
        return None


async def lookup(ip: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Details for `ip`, or for this connection's public address when ip is None."""
    if ip:
        ip = validate_public_ip(ip)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, transport=transport) as client:
        if ip:
            info = await _lookup(client, ip)
            info["is_self"] = False
            return info
        info, ipv6 = await asyncio.gather(_lookup(client, None), _public_ipv6(client))
    info["is_self"] = True
    info["ipv6"] = ipv6
    return info
