"""Internet speed test: latency/jitter, download and upload throughput.

Uses Cloudflare's public speed-test endpoints by default (the same ones behind
speed.cloudflare.com):
    GET  {base}/__down?bytes=N   -> returns N bytes
    POST {base}/__up             -> accepts and discards the body

Results stream as a sequence of event dicts so the UI can draw live progress.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections.abc import AsyncIterator

import httpx

DEFAULT_BASE_URL = "https://speed.cloudflare.com"

SAMPLE_INTERVAL = 0.25  # seconds between live throughput samples
WARMUP = 1.0  # seconds ignored at the start of each phase (TCP slow start)


def mbps(nbytes: int, seconds: float) -> float:
    return (nbytes * 8 / 1_000_000) / seconds if seconds > 0 else 0.0


def jitter(samples: list[float]) -> float:
    """Mean absolute difference between consecutive latency samples."""
    if len(samples) < 2:
        return 0.0
    return statistics.mean(abs(a - b) for a, b in zip(samples, samples[1:]))


class _Counter:
    def __init__(self) -> None:
        self.total = 0


async def _measure(
    phase: str, worker, streams: int, duration: float
) -> AsyncIterator[dict]:
    """Run `streams` copies of worker(counter, deadline) and sample throughput while they run."""
    counter = _Counter()
    start = time.perf_counter()
    deadline = start + duration
    tasks = [asyncio.create_task(worker(counter, deadline)) for _ in range(streams)]
    samples: list[float] = []
    last_bytes, last_t = 0, start
    try:
        while time.perf_counter() < deadline:
            await asyncio.sleep(SAMPLE_INTERVAL)
            if all(t.done() for t in tasks):
                errors = [t.exception() for t in tasks if t.exception()]
                if errors:
                    raise errors[0]
                break
            now = time.perf_counter()
            rate = mbps(counter.total - last_bytes, now - last_t)
            last_bytes, last_t = counter.total, now
            if now - start >= WARMUP:
                samples.append(rate)
            yield {"phase": phase, "type": "sample", "t": round(now - start, 2), "mbps": round(rate, 2)}
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.perf_counter() - start
    result = statistics.mean(samples) if samples else mbps(counter.total, elapsed)
    yield {"phase": phase, "type": "result", "mbps": round(result, 2), "bytes": counter.total}


async def run_speedtest(
    base_url: str = DEFAULT_BASE_URL,
    duration: float = 8.0,
    streams: int = 4,
    latency_pings: int = 12,
    chunk_bytes: int = 25_000_000,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[dict]:
    base_url = base_url.rstrip("/")
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True)
    summary: dict = {}
    try:
        # --- Latency: tiny requests over a warm connection --------------------------
        yield {"phase": "latency", "type": "start"}
        await client.get(f"{base_url}/__down", params={"bytes": 0})  # warm up TLS/connection
        rtts: list[float] = []
        for _ in range(latency_pings):
            t0 = time.perf_counter()
            r = await client.get(f"{base_url}/__down", params={"bytes": 0})
            r.raise_for_status()
            rtt = (time.perf_counter() - t0) * 1000
            rtts.append(rtt)
            yield {"phase": "latency", "type": "sample", "ms": round(rtt, 2)}
        summary["latency_ms"] = round(statistics.median(rtts), 2)
        summary["jitter_ms"] = round(jitter(rtts), 2)
        yield {"phase": "latency", "type": "result", **summary}

        # --- Download ---------------------------------------------------------------
        async def download(counter: _Counter, deadline: float) -> None:
            while time.perf_counter() < deadline:
                async with client.stream("GET", f"{base_url}/__down", params={"bytes": chunk_bytes}) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes():
                        counter.total += len(chunk)
                        if time.perf_counter() >= deadline:
                            return
                        await asyncio.sleep(0)  # let the sampler run even on very fast links

        yield {"phase": "download", "type": "start"}
        async for ev in _measure("download", download, streams, duration):
            if ev["type"] == "result":
                summary["download_mbps"] = ev["mbps"]
            yield ev

        # --- Upload -----------------------------------------------------------------
        payload = os.urandom(1 << 20)  # 1 MiB of incompressible data, sent repeatedly
        piece = 64 * 1024
        upload_size = 8 * len(payload)

        async def upload(counter: _Counter, deadline: float) -> None:
            # Streamed in pieces so progress is counted as it goes; the body always has
            # its full declared length (the task is cancelled at the deadline instead).
            async def body():
                sent = 0
                while sent < upload_size:
                    off = sent % len(payload)
                    data = payload[off : off + piece]
                    sent += len(data)
                    counter.total += len(data)
                    yield data
                    await asyncio.sleep(0)

            while time.perf_counter() < deadline:
                r = await client.post(
                    f"{base_url}/__up",
                    content=body(),
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(upload_size),
                    },
                )
                r.raise_for_status()

        yield {"phase": "upload", "type": "start"}
        async for ev in _measure("upload", upload, streams, duration):
            if ev["type"] == "result":
                summary["upload_mbps"] = ev["mbps"]
            yield ev

        yield {"phase": "done", "type": "result", **summary}
    except httpx.HTTPError as exc:
        yield {"phase": "error", "type": "error", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        if own_client:
            await client.aclose()
