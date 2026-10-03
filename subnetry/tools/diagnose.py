"""Quick and Full health checks: run several tools in sequence and build one report.

  quick -> speed test
  full  -> speed test, Wi-Fi signal & channel analysis, device scan, port check

Progress streams as events. Each tool's own events are forwarded inside a
`step_event`, so the UI can reuse its existing per-tool rendering. The final
`report` event carries the results, recommendations and score.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from ..system import raise_open_file_limit
from . import advisor, netinfo, netscan, speedtest, wifiscan

MODES = {
    "quick": [("speed", "Speed test")],
    "full": [
        ("speed", "Speed test"),
        ("wifi", "Wi-Fi signal & channel analysis"),
        ("devices", "Device discovery"),
        ("ports", "Device port check"),
    ],
}


async def _speed(report: dict) -> AsyncIterator[dict]:
    result: dict = {}
    async for ev in speedtest.run():
        if ev["type"] == "error":
            result = {"error": ev["message"]}
        elif ev["phase"] == "done":
            result = {k: v for k, v in ev.items() if k not in ("phase", "type")}
        yield ev
    report["speed"] = result or {"error": "Speed test returned no result."}


async def _wifi(report: dict) -> AsyncIterator[dict]:
    try:
        report["wifi"] = await wifiscan.scan_wifi()
    except wifiscan.WifiError as exc:
        report["wifi"] = {"error": str(exc), "networks": []}
    yield {"type": "result", "wifi": report["wifi"]}


async def _devices(report: dict) -> AsyncIterator[dict]:
    hosts: dict[str, dict] = {}
    net = {"hosts": []}
    async for ev in netscan.scan_network():
        if ev["type"] == "start":
            net.update(network=ev["network"], gateway=ev["gateway"])
        elif ev["type"] == "host":
            hosts[ev["host"]["ip"]] = ev["host"]
        elif ev["type"] == "error":
            net["error"] = ev["message"]
        elif ev["type"] == "done":
            net["seconds"] = ev["seconds"]
        yield ev
    net["hosts"] = sorted(hosts.values(), key=lambda h: tuple(int(o) for o in h["ip"].split(".")))
    report["network"] = net


async def _ports(report: dict) -> AsyncIterator[dict]:
    hosts = report.get("network", {}).get("hosts", [])
    # Up to 4 hosts at a time x ~100 sockets each, fewer if the OS allows few open files (macOS: 256).
    sem = asyncio.Semaphore(max(1, min(4, (raise_open_file_limit() - 64) // 110)))

    async def one(host: dict) -> dict:
        async with sem:
            host["ports"] = await netscan.scan_ports(host["ip"])
        return host

    done = 0
    for coro in asyncio.as_completed([one(h) for h in hosts]):
        host = await coro
        done += 1
        yield {"type": "ports", "ip": host["ip"], "ports": host["ports"], "done": done, "total": len(hosts)}


STEPS = {"speed": _speed, "wifi": _wifi, "devices": _devices, "ports": _ports}


def new_report(mode: str) -> dict:
    return {
        "id": datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
        "mode": mode,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


async def run_diagnosis(mode: str = "quick") -> AsyncIterator[dict]:
    if mode not in MODES:
        raise ValueError(f"Unknown scan mode: {mode}")
    steps = MODES[mode]
    report = new_report(mode)
    started = time.perf_counter()
    report["system"] = await netinfo.overview()
    yield {"type": "plan", "mode": mode, "id": report["id"],
           "steps": [{"id": sid, "label": label} for sid, label in steps]}

    for sid, label in steps:
        yield {"type": "step", "step": sid, "status": "running"}
        status = "done"
        try:
            async for ev in STEPS[sid](report):
                yield {"type": "step_event", "step": sid, "event": ev}
        except Exception as exc:  # one failing step shouldn't sink the whole report
            status = "error"
            report.setdefault("errors", {})[sid] = str(exc)
            yield {"type": "step_event", "step": sid, "event": {"type": "error", "message": str(exc)}}
        if sid == "speed" and report.get("speed", {}).get("error"):
            status = "error"
        yield {"type": "step", "step": sid, "status": status}

    report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["duration_s"] = round(time.perf_counter() - started, 1)
    report["recommendations"] = advisor.recommend(report)
    report["score"] = advisor.score(report["recommendations"])
    yield {"type": "report", "report": report}
