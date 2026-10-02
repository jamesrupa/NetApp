"""Live Wi-Fi monitor: sample the current connection every second or two.

Shows how signal changes as you move around (to find weak spots) and tracks
which access point (BSSID) you are attached to. That catches roaming between
mesh nodes/APs, and "sticky" clients that cling to a distant AP while a much
stronger one for the same network is nearby.

  Linux   -> `iw dev <iface> link` (fallback: nmcli + /proc/net/wireless)
  Windows -> `netsh wlan show interfaces`
  macOS   -> CoreWLAN via pyobjc if installed, else system_profiler (slow)
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

from ..system import IS_LINUX, IS_MAC, IS_WINDOWS, run_cmd
from . import wifiscan

STICKY_MARGIN_DB = 10  # another AP this much stronger means you probably should have roamed
STICKY_BELOW_DBM = -65  # ...but only worth flagging once your own signal is getting weak
MAX_SECONDS = 3600


# --- parsers ------------------------------------------------------------------------

def parse_netsh_interfaces(text: str) -> dict | None:
    """The connected interface from `netsh wlan show interfaces` (English output)."""
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            key, sep, value = line.partition(" : ")
            if sep:
                fields[key.strip()] = value.strip()
        if fields.get("State", "").lower() != "connected":
            continue
        pct = fields.get("Signal", "").rstrip("%")
        rssi = fields.get("Rssi")
        chan = fields.get("Channel", "")
        band_m = re.match(r"([\d.]+)\s*GHz", fields.get("Band", ""))
        rate = fields.get("Receive rate (Mbps)")
        return conn(
            ssid=fields.get("SSID"),
            bssid=fields.get("AP BSSID") or fields.get("BSSID"),
            signal_dbm=int(rssi) if rssi and rssi.lstrip("-").isdigit() else None,
            signal_percent=int(pct) if pct.isdigit() else None,
            channel=int(chan) if chan.isdigit() else None,
            band=f"{band_m.group(1)} GHz" if band_m else None,
            rx_rate_mbps=float(rate) if rate else None,
            tx_rate_mbps=float(fields["Transmit rate (Mbps)"]) if fields.get("Transmit rate (Mbps)") else None,
            radio=fields.get("Radio type"),
            interface=fields.get("Name"),
        )
    return None


def parse_iw_link(text: str, iface: str | None = None) -> dict | None:
    if "Not connected" in text or "Connected to" not in text:
        return None
    bssid = re.search(r"Connected to ([0-9a-fA-F:]{17})", text)
    ssid = re.search(r"^\s*SSID: (.*)$", text, re.M)
    freq = re.search(r"freq: ([\d.]+)", text)
    sig = re.search(r"signal: (-?\d+) dBm", text)
    rx = re.search(r"rx bitrate: ([\d.]+) MBit/s", text)
    tx = re.search(r"tx bitrate: ([\d.]+) MBit/s", text)
    freq_mhz = int(float(freq.group(1))) if freq else None
    return conn(
        ssid=ssid.group(1) if ssid else None,
        bssid=bssid.group(1) if bssid else None,
        signal_dbm=int(sig.group(1)) if sig else None,
        frequency_mhz=freq_mhz,
        channel=freq_to_channel(freq_mhz),
        rx_rate_mbps=float(rx.group(1)) if rx else None,
        tx_rate_mbps=float(tx.group(1)) if tx else None,
        interface=iface,
    )


def parse_proc_net_wireless(text: str) -> dict[str, int]:
    """Interface -> live signal level in dBm."""
    levels = {}
    for line in text.splitlines()[2:]:
        m = re.match(r"\s*(\S+):\s+\S+\s+[\d.]+\s+(-?\d+)\.?", line)
        if m and int(m.group(2)) < 0:
            levels[m.group(1)] = int(m.group(2))
    return levels


def freq_to_channel(freq: int | None) -> int | None:
    if not freq:
        return None
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5160 <= freq <= 5895:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


def conn(**fields) -> dict:
    """Normalize a connection sample (fills in dBm/percent, band and frequency)."""
    net = wifiscan.make_network(
        fields.pop("ssid", None), fields.pop("bssid", None),
        signal_percent=fields.pop("signal_percent", None), signal_dbm=fields.pop("signal_dbm", None),
        channel=fields.pop("channel", None), freq_mhz=fields.pop("frequency_mhz", None),
        band=fields.pop("band", None), in_use=True, **fields,
    )
    net.pop("security", None)
    net.pop("in_use", None)
    return net


# --- per-OS sampling ----------------------------------------------------------------

async def _linux() -> dict | None:
    try:
        levels = parse_proc_net_wireless(Path("/proc/net/wireless").read_text())
    except OSError:
        levels = {}
    for iface in levels or [None]:
        if iface:
            res = await run_cmd(["iw", "dev", iface, "link"], timeout=3)
            if res and res.ok:
                c = parse_iw_link(res.stdout, iface)
                if c:
                    return c
    # Fallback: NetworkManager's cached view (signal updates less often).
    res = await run_cmd(["nmcli", "-t", "-f", wifiscan.NMCLI_FIELDS, "device", "wifi", "list", "--rescan", "no"], timeout=5)
    if not res or not res.ok:
        if res is None and not levels:
            raise wifiscan.WifiError("Install `iw` or NetworkManager (`nmcli`) to monitor Wi-Fi on Linux.")
        return None
    current = next((n for n in wifiscan.parse_nmcli(res.stdout) if n["in_use"]), None)
    if not current:
        return None
    dbm = next(iter(levels.values()), None)
    return conn(ssid=current["ssid"], bssid=current["bssid"], channel=current["channel"],
                frequency_mhz=current["frequency_mhz"], signal_dbm=dbm,
                signal_percent=None if dbm is not None else current["signal_percent"])


async def _windows() -> dict | None:
    res = await run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=5)
    if res is None:
        raise wifiscan.WifiError("Could not run `netsh`.")
    return parse_netsh_interfaces(res.stdout)


def _corewlan() -> dict | None:
    from CoreWLAN import CWWiFiClient  # type: ignore[import-not-found]  # pip install pyobjc-framework-CoreWLAN

    iface = CWWiFiClient.sharedWiFiClient().interface()
    if iface is None or not iface.rssiValue():
        return None
    ch = iface.wlanChannel()
    band = {1: "2.4 GHz", 2: "5 GHz", 3: "6 GHz"}.get(ch.channelBand()) if ch else None
    return conn(ssid=iface.ssid(), bssid=iface.bssid(), signal_dbm=int(iface.rssiValue()),
                channel=int(ch.channelNumber()) if ch else None, band=band,
                tx_rate_mbps=float(iface.transmitRate() or 0) or None,
                noise_dbm=int(iface.noiseMeasurement()) or None, interface=iface.interfaceName())


async def _mac() -> dict | None:
    try:
        return await asyncio.to_thread(_corewlan)
    except ImportError:
        pass
    data = await wifiscan._scan_mac()  # slow (several seconds) but always available
    return next((dict(n, bssid=None) for n in data if n["in_use"]), None)


async def current_connection() -> dict | None:
    if IS_LINUX:
        return await _linux()
    if IS_WINDOWS:
        return await _windows()
    if IS_MAC:
        return await _mac()
    raise wifiscan.WifiError("Wi-Fi monitoring is not supported on this operating system.")


# --- roaming analysis -----------------------------------------------------------------

class RoamTracker:
    """Turns a stream of samples (and periodic scans) into roam / sticky-client events."""

    def __init__(self) -> None:
        self.last: dict | None = None
        self.connected = None
        self.sticky_flagged: dict[tuple, float] = {}

    def feed(self, c: dict | None, t: float) -> list[dict]:
        events = []
        if not c:
            if self.connected:
                events.append({"type": "event", "kind": "disconnect", "t": t,
                               "message": f"Lost connection to {self.last['ssid'] or 'Wi-Fi'}"})
            self.connected = False
            return events
        if self.connected is False:
            events.append({"type": "event", "kind": "reconnect", "t": t, "message": f"Reconnected to {c['ssid']}"})
        last = self.last
        if last and self.connected is not False:
            if c["ssid"] != last["ssid"]:
                events.append({"type": "event", "kind": "network_change", "t": t,
                               "message": f"Switched networks: {last['ssid']} → {c['ssid']}"})
            elif c.get("bssid") and last.get("bssid") and c["bssid"] != last["bssid"]:
                gain = (c["signal_dbm"] - last["signal_dbm"]) if c.get("signal_dbm") is not None and last.get("signal_dbm") is not None else None
                band = f" ({last['band']} → {c['band']})" if last.get("band") != c.get("band") else ""
                events.append({
                    "type": "event", "kind": "roam", "t": t,
                    "from_bssid": last["bssid"], "to_bssid": c["bssid"],
                    "from_dbm": last.get("signal_dbm"), "to_dbm": c.get("signal_dbm"),
                    "message": f"Roamed to access point {c['bssid']}{band}"
                               + (f": signal {last['signal_dbm']} → {c['signal_dbm']} dBm ({gain:+d} dB)" if gain is not None else ""),
                    "good": gain is None or gain >= 0,
                })
        self.last, self.connected = c, True
        return events

    def check_neighbors(self, nets: list[dict], c: dict | None, t: float) -> list[dict]:
        """After a scan: is a much stronger AP for this same network available?"""
        if not c or c.get("signal_dbm") is None or not c.get("bssid"):
            return []
        same = [n for n in nets if n["ssid"] == c["ssid"] and n.get("bssid") and n["bssid"] != c["bssid"]
                and n.get("signal_dbm") is not None]
        if not same:
            return []
        best = max(same, key=lambda n: n["signal_dbm"])
        margin = best["signal_dbm"] - c["signal_dbm"]
        if margin < STICKY_MARGIN_DB or c["signal_dbm"] > STICKY_BELOW_DBM:
            return []
        key = (c["bssid"], best["bssid"])
        if t - self.sticky_flagged.get(key, -1e9) < 60:
            return []
        self.sticky_flagged[key] = t
        return [{
            "type": "event", "kind": "sticky", "t": t, "better_bssid": best["bssid"],
            "message": (f"Sticky connection: still on {c['bssid']} at {c['signal_dbm']} dBm although "
                        f"{best['bssid']} ({best.get('band') or '?'}, ch {best.get('channel') or '?'}) is {margin} dB stronger. "
                        "Toggle Wi-Fi off/on to re-associate; if it keeps happening, enable 802.11k/v/r (fast roaming) "
                        "on the router or lower the device's 'roaming aggressiveness' threshold."),
        }]


def same_network_aps(nets: list[dict], c: dict | None) -> list[dict]:
    if not c:
        return []
    aps = [dict(n, is_current=bool(c.get("bssid") and n.get("bssid") == c["bssid"]))
           for n in nets if n["ssid"] and n["ssid"] == c["ssid"]]
    return sorted(aps, key=lambda n: -(n.get("signal_dbm") or -200))


# --- the monitor loop ---------------------------------------------------------------

async def monitor(interval: float = 1.0, scan_every: float = 30.0, max_seconds: float = MAX_SECONDS) -> AsyncIterator[dict]:
    tracker = RoamTracker()
    start = time.perf_counter()
    next_scan = start + 2
    scan_task: asyncio.Task | None = None
    scanning_enabled = True
    latest: dict | None = None
    try:
        while (now := time.perf_counter()) - start < max_seconds:
            t = round(now - start, 2)
            try:
                latest = await current_connection()
            except wifiscan.WifiError as exc:
                yield {"type": "error", "message": str(exc)}
                return
            yield {"type": "sample", "t": t, "connected": bool(latest), **(latest or {})}
            for ev in tracker.feed(latest, t):
                yield ev

            if scan_task and scan_task.done():
                try:
                    nets = scan_task.result()["networks"]
                    yield {"type": "aps", "t": t, "aps": same_network_aps(nets, latest), "nearby_total": len(nets)}
                    for ev in tracker.check_neighbors(nets, latest, t):
                        yield ev
                except wifiscan.WifiError as exc:
                    scanning_enabled = False
                    yield {"type": "aps", "t": t, "aps": [], "error": str(exc)}
                scan_task = None
            if scanning_enabled and scan_task is None and now >= next_scan:
                scan_task = asyncio.create_task(wifiscan.scan_wifi())
                next_scan = now + scan_every
            await asyncio.sleep(max(0.1, interval - (time.perf_counter() - now)))
    finally:
        if scan_task:
            scan_task.cancel()
