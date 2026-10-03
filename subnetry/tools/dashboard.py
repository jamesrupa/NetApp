"""Home dashboard: remembered "last results" plus a live connection-quality stream.

The other tools report their results here (last speed test, last health check,
last device scan) so the dashboard can show them at a glance, even after a restart:
they're kept in a small JSON file next to the saved reports.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from ..system import IS_MAC
from . import netinfo, netscan
from .report import reports_dir

INTERNET_PROBE = ("1.1.1.1", 443)  # Cloudflare: answers TCP on 443 almost everywhere
_lock = threading.Lock()


# --- remembered results ---------------------------------------------------------------

def state_path() -> Path:
    return reports_dir() / ".subnetry-state.json"


def load_state() -> dict:
    for path in (state_path(), reports_dir() / ".netapp-state.json"):  # (second: saved under the old name)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def record(key: str, data: dict) -> None:
    """Remember the latest result of a tool (best effort: never breaks the tool itself)."""
    with _lock:
        state = load_state()
        state[key] = {**data, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            path = state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass


def record_speed(result: dict) -> None:
    keep = ("download_mbps", "upload_mbps", "latency_ms", "jitter_ms", "packet_loss", "engine", "server", "isp", "result_url")
    record("speed", {k: result.get(k) for k in keep if result.get(k) is not None})


def record_health(report: dict) -> None:
    recs = [r for r in report.get("recommendations", []) if r["severity"] in ("critical", "warning")]
    record("health", {
        "id": report.get("id"), "mode": report.get("mode"), "score": report.get("score"),
        "top": [{k: r[k] for k in ("severity", "category", "title", "action")} for r in recs[:3]],
    })


def record_devices(network: str | None, hosts: int) -> None:
    record("devices", {"network": network, "count": hosts})


# --- live probes ------------------------------------------------------------------------

async def tcp_latency(host: str, port: int, timeout: float = 2.0) -> float | None:
    """Time to open a TCP connection (works without admin rights, unlike raw ICMP)."""
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except ConnectionRefusedError:
        return round((time.perf_counter() - t0) * 1000, 1)  # a refusal is still a round trip
    except (asyncio.TimeoutError, OSError):
        return None
    ms = round((time.perf_counter() - t0) * 1000, 1)
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return ms


async def gateway_latency(gateway: str | None) -> float | None:
    if not gateway:
        return None
    ms = await netscan.ping(gateway, 1000)
    if ms is not None:
        return ms
    for port in (53, 80, 443):  # routers that ignore ping usually answer one of these
        ms = await tcp_latency(gateway, port, 1.0)
        if ms is not None:
            return ms
    return None


async def dns_latency(name: str = "example.com") -> float | None:
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(name, 443, type=socket.SOCK_STREAM), 3)
    except (asyncio.TimeoutError, OSError):
        return None
    return round((time.perf_counter() - t0) * 1000, 1)


async def wifi_snapshot() -> dict | None:
    """The current Wi-Fi connection, or None (wired / no Wi-Fi / unsupported)."""
    from . import wifimonitor, wifiscan

    if IS_MAC:
        from . import macos_helper
        try:
            data = await asyncio.to_thread(macos_helper.call, "current", 20)
            return macos_helper.to_connection(data.get("current"))
        except macos_helper.HelperUnavailable:
            pass
    try:
        return await wifimonitor.current_connection()
    except (wifiscan.WifiError, Exception):
        return None


async def summary() -> dict:
    overview = await netinfo.overview()
    net = overview["networks"][0] if overview["networks"] else None
    return {
        "hostname": overview["hostname"],
        "os": overview["os"],
        "local_ip": net["address"] if net else None,
        "network": net["network"] if net else None,
        "interface": net["interface"] if net else None,
        "gateway": overview["gateway"],
        "dns_servers": overview["dns_servers"],
        "last": load_state(),
    }


async def live(interval: float = 2.0, wifi_every: float = 10.0, max_seconds: float = 3600) -> AsyncIterator[dict]:
    """Every `interval` s: latency to the router and the internet; Wi-Fi signal every `wifi_every` s."""
    gateway = await netinfo.default_gateway()
    started = time.perf_counter()
    next_wifi = 0.0
    wifi_task: asyncio.Task | None = None
    try:
        while (now := time.perf_counter()) - started < max_seconds:
            gw, inet, dns = await asyncio.gather(
                gateway_latency(gateway), tcp_latency(*INTERNET_PROBE), dns_latency())
            ev = {"type": "sample", "t": round(now - started, 1), "gateway": gateway,
                  "gateway_ms": gw, "internet_ms": inet, "dns_ms": dns, "online": inet is not None}
            if wifi_task and wifi_task.done():
                ev["wifi"] = wifi_task.result() if not wifi_task.exception() else None
                ev["wifi_checked"] = True
                wifi_task = None
            if wifi_task is None and now >= next_wifi:
                wifi_task = asyncio.create_task(wifi_snapshot())
                next_wifi = now + wifi_every
            yield ev
            await asyncio.sleep(max(0.2, interval - (time.perf_counter() - now)))
    finally:
        if wifi_task:
            wifi_task.cancel()
