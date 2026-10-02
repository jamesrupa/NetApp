"""Wi-Fi scanner: list nearby access points using the OS's own wireless tooling.

  Linux   -> nmcli (NetworkManager)
  Windows -> netsh wlan
  macOS   -> system_profiler SPAirPortDataType (SSIDs need Location Services on 14+)

Every parser returns the same normalized shape, so the UI and channel analysis
don't care which OS produced the data.
"""

from __future__ import annotations

import json
import re

from ..system import IS_LINUX, IS_MAC, IS_WINDOWS, run_cmd

CHANNELS_24 = [1, 6, 11]  # the only non-overlapping 20 MHz channels in 2.4 GHz
# 5 GHz channels that need no radar detection (DFS), so every router supports them.
CHANNELS_5_NON_DFS = [36, 40, 44, 48, 149, 153, 157, 161, 165]


class WifiError(RuntimeError):
    pass


# --- unit helpers -----------------------------------------------------------------

def percent_to_dbm(pct: int) -> int:
    """Approximation used by Windows/NetworkManager: 0% ~ -100 dBm, 100% ~ -50 dBm."""
    return round(pct / 2 - 100)


def dbm_to_percent(dbm: int) -> int:
    return max(0, min(100, 2 * (dbm + 100)))


def band_for(channel: int | None, freq_mhz: int | None = None) -> str | None:
    if freq_mhz:
        if freq_mhz < 3000:
            return "2.4 GHz"
        if freq_mhz < 5925:
            return "5 GHz"
        return "6 GHz"
    if channel is None:
        return None
    return "2.4 GHz" if channel <= 14 else "5 GHz"


def channel_to_freq(channel: int, band: str | None) -> int | None:
    if band == "2.4 GHz":
        return 2484 if channel == 14 else 2407 + 5 * channel
    if band == "5 GHz":
        return 5000 + 5 * channel
    if band == "6 GHz":
        return 5950 + 5 * channel
    return None


def make_network(
    ssid: str | None,
    bssid: str | None = None,
    *,
    signal_percent: int | None = None,
    signal_dbm: int | None = None,
    channel: int | None = None,
    freq_mhz: int | None = None,
    band: str | None = None,
    security: str | None = None,
    in_use: bool = False,
    **extra,
) -> dict:
    if signal_dbm is None and signal_percent is not None:
        signal_dbm = percent_to_dbm(signal_percent)
    if signal_percent is None and signal_dbm is not None:
        signal_percent = dbm_to_percent(signal_dbm)
    band = band or band_for(channel, freq_mhz)
    if freq_mhz is None and channel is not None:
        freq_mhz = channel_to_freq(channel, band)
    return {
        "ssid": ssid or "",
        "hidden": not ssid,
        "bssid": bssid.upper() if bssid else None,
        "signal_percent": signal_percent,
        "signal_dbm": signal_dbm,
        "channel": channel,
        "frequency_mhz": freq_mhz,
        "band": band,
        "security": security or "Open",
        "in_use": in_use,
        **extra,
    }


# --- Linux: nmcli -----------------------------------------------------------------

NMCLI_FIELDS = "IN-USE,BSSID,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY"


def _split_nmcli(line: str) -> list[str]:
    """Split terse nmcli output on ':' while honouring '\\:' escapes."""
    parts = re.split(r"(?<!\\):", line)
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in parts]


def parse_nmcli(text: str) -> list[dict]:
    nets = []
    for line in text.splitlines():
        if not line.strip():
            continue
        f = _split_nmcli(line)
        if len(f) < 8:
            continue
        in_use, bssid, ssid, chan, freq, rate, signal, security = f[:8]
        freq_m = re.search(r"\d+", freq)
        nets.append(make_network(
            ssid,
            bssid or None,
            signal_percent=int(signal) if signal.isdigit() else None,
            channel=int(chan) if chan.isdigit() else None,
            freq_mhz=int(freq_m.group()) if freq_m else None,
            security=None if security.strip() in ("", "--") else security.strip(),
            in_use=in_use.strip() == "*",
            rate=rate or None,
        ))
    return nets


async def _scan_linux() -> list[dict]:
    res = await run_cmd(
        ["nmcli", "-t", "-f", NMCLI_FIELDS, "device", "wifi", "list", "--rescan", "yes"], timeout=30
    )
    if res is None:
        raise WifiError("`nmcli` (NetworkManager) is required for Wi-Fi scanning on Linux.")
    if not res.ok:
        raise WifiError(res.stderr.strip() or "nmcli failed to scan for Wi-Fi networks.")
    return parse_nmcli(res.stdout)


# --- Windows: netsh ---------------------------------------------------------------

def parse_netsh_networks(text: str, connected_bssid: str | None = None) -> list[dict]:
    nets: list[dict] = []
    ssid = auth = enc = None
    cur: dict | None = None

    def flush():
        if cur is not None:
            nets.append(make_network(
                ssid, cur.get("bssid"),
                signal_percent=cur.get("signal"),
                channel=cur.get("channel"),
                band=cur.get("band"),
                security=" / ".join(x for x in (auth, enc) if x and x != "None") or None,
                in_use=bool(connected_bssid and cur.get("bssid", "").upper() == connected_bssid.upper()),
                radio=cur.get("radio"),
            ))

    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if re.match(r"^SSID \d+$", key):
            flush()
            cur = None
            ssid, auth, enc = value, None, None
        elif key == "Authentication":
            auth = value
        elif key == "Encryption":
            enc = value
        elif re.match(r"^BSSID \d+$", key):
            flush()
            cur = {"bssid": value}
        elif cur is not None:
            if key == "Signal":
                cur["signal"] = int(value.rstrip("%")) if value.rstrip("%").isdigit() else None
            elif key == "Channel" and value.isdigit():
                cur["channel"] = int(value)
            elif key == "Band":
                m = re.match(r"([\d.]+)\s*GHz", value)
                cur["band"] = f"{m.group(1)} GHz" if m else None
            elif key == "Radio type":
                cur["radio"] = value
    flush()
    return nets


async def _scan_windows() -> list[dict]:
    iface = await run_cmd(["netsh", "wlan", "show", "interfaces"])
    connected = None
    if iface:
        m = re.search(r"^\s*(?:AP )?BSSID\s*:\s*(\S+)", iface.stdout, re.M)
        connected = m.group(1) if m else None
    res = await run_cmd(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=20)
    if res is None:
        raise WifiError("Could not run `netsh`.")
    nets = parse_netsh_networks(res.stdout, connected)
    if not nets and (not res.ok or "location" in res.stdout.lower()):
        raise WifiError(
            (res.stdout.strip() or res.stderr.strip() or "netsh returned no networks.")
            + "\n\nOn Windows 11, Wi-Fi scanning needs Location access: Settings > Privacy & security > Location."
        )
    return nets


# --- macOS: system_profiler -------------------------------------------------------

def _mac_security(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.replace("spairport_security_mode_", "").replace("_", " ").strip()
    return "Open" if s == "none" else s.upper().replace("PERSONAL", "Personal").replace("ENTERPRISE", "Enterprise")


def _mac_network(entry: dict, in_use: bool) -> dict:
    chan_raw = str(entry.get("spairport_network_channel", ""))
    chan_m = re.match(r"(\d+)", chan_raw)
    band = None
    if "6GHz" in chan_raw:
        band = "6 GHz"
    elif "5GHz" in chan_raw:
        band = "5 GHz"
    elif "2GHz" in chan_raw:
        band = "2.4 GHz"
    sig_m = re.search(r"(-?\d+)\s*dBm", str(entry.get("spairport_signal_noise", "")))
    noise_m = re.search(r"/\s*(-?\d+)\s*dBm", str(entry.get("spairport_signal_noise", "")))
    return make_network(
        entry.get("_name"),
        None,
        signal_dbm=int(sig_m.group(1)) if sig_m else None,
        channel=int(chan_m.group(1)) if chan_m else None,
        band=band,
        security=_mac_security(entry.get("spairport_security_mode")),
        in_use=in_use,
        noise_dbm=int(noise_m.group(1)) if noise_m else None,
        radio=entry.get("spairport_network_phymode"),
    )


def parse_system_profiler(data: dict) -> list[dict]:
    nets = []
    for section in data.get("SPAirPortDataType", []):
        for iface in section.get("spairport_airport_interfaces", []):
            current = iface.get("spairport_current_network_information")
            if current:
                nets.append(_mac_network(current, True))
            for entry in iface.get("spairport_airport_other_local_wireless_networks", []):
                nets.append(_mac_network(entry, False))
    return nets


async def _scan_mac() -> list[dict]:
    res = await run_cmd(["system_profiler", "SPAirPortDataType", "-json"], timeout=40)
    if res is None or not res.ok:
        raise WifiError("system_profiler failed to report Wi-Fi networks.")
    try:
        return parse_system_profiler(json.loads(res.stdout))
    except json.JSONDecodeError as exc:
        raise WifiError(f"Could not parse system_profiler output: {exc}") from exc


# --- analysis ---------------------------------------------------------------------

def _overlap_24(a: int, b: int) -> float:
    """How much two 20 MHz 2.4 GHz channels interfere (1.0 = same, 0 = >=5 apart)."""
    return max(0.0, 1 - abs(a - b) / 5)


def channel_report(nets: list[dict]) -> dict:
    """Per-band channel usage plus a suggested least-congested channel."""
    usage: dict[str, dict[int, int]] = {}
    for n in nets:
        if n["band"] and n["channel"]:
            usage.setdefault(n["band"], {})
            usage[n["band"]][n["channel"]] = usage[n["band"]].get(n["channel"], 0) + 1

    recommendations = {}
    nets_24 = [n for n in nets if n["band"] == "2.4 GHz" and n["channel"]]
    if nets_24:
        scores = {
            ch: round(sum(_overlap_24(ch, n["channel"]) * (n["signal_percent"] or 50) / 100 for n in nets_24), 2)
            for ch in CHANNELS_24
        }
        best = min(scores, key=scores.get)
        recommendations["2.4 GHz"] = {"channel": best, "scores": scores}
    if "5 GHz" in usage:
        used = usage["5 GHz"]
        best5 = min(CHANNELS_5_NON_DFS, key=lambda c: (used.get(c, 0), CHANNELS_5_NON_DFS.index(c)))
        recommendations["5 GHz"] = {"channel": best5, "networks_on_channel": used.get(best5, 0)}
    return {
        "usage": {band: dict(sorted(ch.items())) for band, ch in usage.items()},
        "recommendations": recommendations,
    }


async def scan_wifi() -> dict:
    if IS_LINUX:
        nets = await _scan_linux()
    elif IS_WINDOWS:
        nets = await _scan_windows()
    elif IS_MAC:
        nets = await _scan_mac()
    else:
        raise WifiError("Wi-Fi scanning is not supported on this operating system yet.")
    nets.sort(key=lambda n: (not n["in_use"], -(n["signal_percent"] or 0)))
    return {"networks": nets, "channels": channel_report(nets)}
