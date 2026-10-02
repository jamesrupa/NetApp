import asyncio
import json

from fastapi.testclient import TestClient

from netapp import server
from netapp.tools import dashboard


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("NETAPP_REPORTS_DIR", str(tmp_path))
    dashboard.record_speed({"download_mbps": 296.0, "upload_mbps": 20.8, "latency_ms": 11, "jitter_ms": 0.5,
                            "engine": "Speedtest.net (Ookla)", "phase": "done", "type": "result"})
    dashboard.record_devices("192.168.1.0/24", 7)
    dashboard.record_health({"id": "x", "mode": "full", "score": {"value": 65, "grade": "Fair"},
                             "recommendations": [
                                 {"severity": "good", "category": "Speed", "title": "Fast", "detail": "", "action": ""},
                                 {"severity": "warning", "category": "Wi-Fi", "title": "Change channel", "detail": "", "action": "Do it"}]})
    st = dashboard.load_state()
    assert st["speed"]["download_mbps"] == 296.0 and "phase" not in st["speed"] and st["speed"]["at"]
    assert st["devices"] == {"network": "192.168.1.0/24", "count": 7, "at": st["devices"]["at"]}
    assert st["health"]["score"]["value"] == 65 and [t["title"] for t in st["health"]["top"]] == ["Change channel"]


def test_corrupt_state_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("NETAPP_REPORTS_DIR", str(tmp_path))
    (tmp_path / ".netapp-state.json").write_text("{not json")
    assert dashboard.load_state() == {}
    dashboard.record_devices("10.0.0.0/24", 3)
    assert dashboard.load_state()["devices"]["count"] == 3


def test_tcp_latency_to_local_listener():
    async def run():
        srv = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            up = await dashboard.tcp_latency("127.0.0.1", port)
        closed = await dashboard.tcp_latency("127.0.0.1", port)  # refused = still a round trip
        return up, closed
    up, closed = asyncio.run(run())
    assert up is not None and up >= 0 and closed is not None


def test_live_stream(monkeypatch):
    async def fake_gw():
        return "192.168.1.1"

    async def fast(*_a, **_k):
        return 4.2

    async def wifi():
        return {"ssid": "HomeNet", "signal_dbm": -55}
    monkeypatch.setattr(dashboard.netinfo, "default_gateway", fake_gw)
    monkeypatch.setattr(dashboard, "gateway_latency", fast)
    monkeypatch.setattr(dashboard, "tcp_latency", fast)
    monkeypatch.setattr(dashboard, "dns_latency", fast)
    monkeypatch.setattr(dashboard, "wifi_snapshot", wifi)

    async def run():
        return [ev async for ev in dashboard.live(interval=0.2, wifi_every=0.2, max_seconds=1.2)]
    events = asyncio.run(run())
    assert events[0]["gateway"] == "192.168.1.1" and events[0]["internet_ms"] == 4.2 and events[0]["online"]
    assert any(e.get("wifi", {}).get("ssid") == "HomeNet" for e in events)


def test_dashboard_endpoint_and_speed_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("NETAPP_REPORTS_DIR", str(tmp_path))

    async def fake_run(*_a, **_k):
        yield {"phase": "done", "type": "result", "download_mbps": 100.0, "upload_mbps": 10.0, "latency_ms": 9.0, "jitter_ms": 1.0}
    monkeypatch.setattr(server.speedtest, "run", fake_run)
    client = TestClient(server.app)
    with client.stream("GET", "/api/speedtest", params={"engine": "cloudflare"}) as r:
        r.read()
    data = client.get("/api/dashboard").json()
    assert {"hostname", "local_ip", "gateway", "last"} <= data.keys()
    assert data["last"]["speed"]["download_mbps"] == 100.0
