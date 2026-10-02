"""FastAPI app: JSON/SSE API for each tool plus the static web UI.

Long-running tools (speed test, network scan) stream Server-Sent Events so the
browser can show live progress through a plain `EventSource`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .tools import netinfo, netscan, speedtest, wifiscan

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="NetApp", version=__version__)


@app.middleware("http")
async def block_cross_site(request: Request, call_next):
    # The API runs scans on request, so refuse calls initiated by other websites.
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
    return await call_next(request)


def sse(events: AsyncIterator[dict]) -> StreamingResponse:
    async def stream():
        try:
            async for ev in events:
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:  # report the failure to the UI instead of dropping the stream
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- API --------------------------------------------------------------------------

@app.get("/api/overview")
async def overview():
    return await netinfo.overview()


@app.get("/api/public-ip")
async def public_ip():
    try:
        return await netinfo.public_ip()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not determine public IP: {exc}") from exc


@app.get("/api/speedtest")
async def run_speedtest(
    duration: float = Query(8.0, ge=2, le=30),
    streams: int = Query(4, ge=1, le=16),
):
    return sse(speedtest.run_speedtest(duration=duration, streams=streams))


@app.get("/api/scan")
async def scan(cidr: str | None = None, timeout: float = Query(1.0, ge=0.2, le=5)):
    # Validation errors (bad CIDR, public range, too large) arrive as an "error" event,
    # since EventSource can't read the body of an HTTP error response.
    return sse(netscan.scan_network(cidr, timeout=timeout))


@app.get("/api/ports")
async def ports(host: str):
    try:
        return await netscan.scan_ports(host)
    except netscan.ScanError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/wifi")
async def wifi():
    try:
        return await wifiscan.scan_wifi()
    except wifiscan.WifiError as exc:
        raise HTTPException(503, str(exc)) from exc


# --- UI ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
