"""MAC address vendor lookup (OUI), fully offline once a database is available.

A MAC address starts with an Organizationally Unique Identifier assigned by the
IEEE to the manufacturer. Sources, best first:
  1. The IEEE registry (MA-L, MA-M and MA-S), downloaded on request and cached.
  2. Nmap's `nmap-mac-prefixes` (ships with Nmap, which Subnetry already uses).
Longer prefixes (36- and 28-bit blocks) win over 24-bit ones, as they should.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from pathlib import Path

import httpx

from .report import reports_dir

IEEE_SOURCES = [
    ("MA-L", "https://standards-oui.ieee.org/oui/oui.csv"),
    ("MA-M", "https://standards-oui.ieee.org/oui28/mam.csv"),
    ("MA-S", "https://standards-oui.ieee.org/oui36/oui36.csv"),
]
NMAP_PATHS = [
    "/usr/share/nmap/nmap-mac-prefixes", "/usr/local/share/nmap/nmap-mac-prefixes",
    "/opt/homebrew/share/nmap/nmap-mac-prefixes",
    r"C:\Program Files (x86)\Nmap\nmap-mac-prefixes", r"C:\Program Files\Nmap\nmap-mac-prefixes",
]

_lock = threading.Lock()
_db: dict[str, str] | None = None
_source: str | None = None


def cache_path() -> Path:
    return reports_dir() / ".oui-cache.json"


def normalize(mac: str) -> str | None:
    """Hex digits only, upper case (accepts aa:bb:cc, AA-BB-CC, aabb.cc00.1122, prefixes)."""
    hexdigits = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    return hexdigits.upper() if 6 <= len(hexdigits) <= 12 else None


def parse_nmap(text: str) -> dict[str, str]:
    db = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        prefix, _, name = line.partition(" ")
        if re.fullmatch(r"[0-9A-Fa-f]{6,9}", prefix) and name:
            db[prefix.upper()] = name.strip()
    return db


def parse_ieee_csv(text: str) -> dict[str, str]:
    db = {}
    for row in csv.DictReader(io.StringIO(text)):
        assignment = (row.get("Assignment") or "").strip().upper()
        name = (row.get("Organization Name") or "").strip()
        if re.fullmatch(r"[0-9A-F]{6,9}", assignment) and name:
            db[assignment] = name
    return db


def _nmap_file() -> Path | None:
    from .nmapscan import find_nmap

    candidates = [Path(p) for p in NMAP_PATHS]
    nmap = find_nmap()
    if nmap:  # e.g. /opt/homebrew/bin/nmap -> /opt/homebrew/share/nmap/nmap-mac-prefixes
        exe = Path(nmap).resolve()
        candidates[:0] = [exe.parent.parent / "share" / "nmap" / "nmap-mac-prefixes", exe.parent / "nmap-mac-prefixes"]
    return next((p for p in candidates if p.is_file()), None)


def load(force: bool = False) -> tuple[dict[str, str], str | None]:
    global _db, _source
    with _lock:
        if _db is not None and not force:
            return _db, _source
        db, source = {}, None
        try:
            cached = json.loads(cache_path().read_text(encoding="utf-8"))
            db, source = cached["entries"], f"IEEE registry (downloaded {cached.get('date', '?')})"
        except (OSError, ValueError, KeyError):
            pass
        if not db:
            path = _nmap_file()
            if path:
                db, source = parse_nmap(path.read_text(encoding="utf-8", errors="replace")), "Nmap's vendor list"
        _db, _source = db, source
        return db, source


def lookup_prefix(mac_hex: str, db: dict[str, str]) -> tuple[str, str] | None:
    for length in (9, 7, 6):  # most specific block first
        name = db.get(mac_hex[:length])
        if name:
            return mac_hex[:length], name
    return None


def lookup(mac: str) -> dict:
    hexdigits = normalize(mac)
    if not hexdigits:
        return {"input": mac, "valid": False, "error": "Enter a MAC address (e.g. 3C:22:FB:12:34:56) or its first 6 digits."}
    first = int(hexdigits[:2], 16)
    formatted = ":".join(hexdigits[i:i + 2] for i in range(0, len(hexdigits) - len(hexdigits) % 2, 2))
    result = {
        "input": mac, "valid": True, "mac": formatted,
        "oui": ":".join(hexdigits[i:i + 2] for i in range(0, 6, 2)),
        # "Locally administered" bit: made up by the device rather than burned in at the factory.
        "randomized": bool(first & 0x02) and not first & 0x01,
        "multicast": bool(first & 0x01),
        "broadcast": hexdigits == "F" * 12,
        "vendor": None, "matched_prefix": None,
    }
    db, source = load()
    result["source"] = source
    hit = lookup_prefix(hexdigits, db) if db else None
    if hit:
        result["matched_prefix"], result["vendor"] = hit
        result["block"] = {6: "MA-L (24-bit, large block)", 7: "MA-M (28-bit, medium block)", 9: "MA-S (36-bit, small block)"}[len(hit[0])]
    if result["broadcast"]:
        result["note"] = "FF:FF:FF:FF:FF:FF is the broadcast address: frames sent to it reach every device on the network."
    elif result["multicast"]:
        result["note"] = "This is a multicast address (lowest bit of the first byte set): it addresses a group of devices, not one."
    elif result["randomized"] and not hit:
        result["note"] = ("This is a private (randomized) address: the second-lowest bit of the first byte is set, meaning the "
                          "device made it up for privacy. Phones, tablets and laptops do this per network, so it has no vendor.")
    elif not hit and not db:
        result["note"] = "No vendor database yet: install Nmap or press \"Update vendor database\"."
    elif not hit:
        result["note"] = "No vendor registered for this prefix (it may be unassigned, or registered privately)."
    return result


def vendor_for(mac: str | None) -> str | None:
    """Vendor name for the network scanner (no network access; None if unknown)."""
    hexdigits = normalize(mac or "")
    if not hexdigits:
        return None
    db, _ = load()
    hit = lookup_prefix(hexdigits, db) if db else None
    return hit[1] if hit else None


def status() -> dict:
    db, source = load()
    return {"entries": len(db), "source": source}


async def update_from_ieee(transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Download the full IEEE registry and cache it (about 6 MB, a few seconds)."""
    entries: dict[str, str] = {}
    errors = []
    headers = {"User-Agent": "Mozilla/5.0 (Subnetry network toolkit)"}  # IEEE rejects some non-browser clients
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=headers, transport=transport) as client:
        for registry, url in IEEE_SOURCES:
            try:
                r = await client.get(url)
                r.raise_for_status()
                entries.update(parse_ieee_csv(r.text))
            except httpx.HTTPError as exc:
                errors.append(f"{registry}: {exc}")
    if not entries:
        raise RuntimeError("Couldn't download the IEEE registry. " + "; ".join(errors))
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": time.strftime("%Y-%m-%d"), "entries": entries}), encoding="utf-8")
    load(force=True)
    return {**status(), "warnings": errors}
