import asyncio
import json
import os
import stat
import sys
import textwrap

import pytest

from subnetry.tools import advisor, ookla, speedtest

SERVER = {"id": 1234, "host": "speedtest.example.net", "port": 8080, "name": "Example ISP",
          "location": "Austin, TX", "country": "United States", "ip": "198.51.100.1"}
JSONL = [
    {"type": "testStart", "isp": "Example ISP", "server": SERVER},
    {"type": "ping", "ping": {"jitter": 0.4, "latency": 11.2, "progress": 0.5}},
    {"type": "ping", "ping": {"jitter": 0.5, "latency": 10.9, "progress": 1.0}},
    {"type": "download", "download": {"bandwidth": 12_500_000, "bytes": 1_000_000, "elapsed": 250, "progress": 0.1}},
    {"type": "download", "download": {"bandwidth": 37_500_000, "bytes": 30_000_000, "elapsed": 5000, "progress": 0.6}},
    {"type": "upload", "upload": {"bandwidth": 2_500_000, "bytes": 500_000, "elapsed": 400, "progress": 0.2}},
    {"type": "result", "ping": {"jitter": 0.5, "latency": 10.9, "low": 10.1, "high": 12.3},
     "download": {"bandwidth": 37_000_000, "bytes": 300_000_000, "elapsed": 10000},
     "upload": {"bandwidth": 2_600_000, "bytes": 20_000_000, "elapsed": 8000},
     "packetLoss": 0.8, "isp": "Example ISP",
     "interface": {"internalIp": "192.168.1.12", "name": "en0", "externalIp": "203.0.113.7", "isVpn": False},
     "server": SERVER, "result": {"id": "abc", "url": "https://www.speedtest.net/result/c/abc", "persisted": True}},
]


def test_translator_maps_ookla_events():
    tr = ookla.OoklaTranslator()
    events = [ev for d in JSONL for ev in tr.feed(json.dumps(d))]
    assert events[0]["type"] == "server" and events[0]["server"]["location"] == "Austin, TX"
    assert [e["phase"] for e in events if e["type"] == "start"] == ["latency", "download", "upload"]
    dl = [e for e in events if e["phase"] == "download" and e["type"] == "sample"]
    assert dl[-1] == {"phase": "download", "type": "sample", "t": 5.0, "mbps": 300.0, "progress": 0.6}
    done = events[-1]
    assert done["phase"] == "done" and done["download_mbps"] == 296.0 and done["upload_mbps"] == 20.8
    assert done["latency_ms"] == 10.9 and done["packet_loss"] == 0.8
    assert done["result_url"].startswith("https://www.speedtest.net/") and done["engine"] == "Speedtest.net (Ookla)"
    assert tr.feed("not json") == [] and tr.feed("") == []
    err = tr.feed(json.dumps({"type": "log", "level": "error", "message": "Cannot read: Timeout"}))
    assert err == [{"phase": "error", "type": "error", "message": "Cannot read: Timeout"}]


def test_parse_servers():
    text = json.dumps({"type": "serverList", "servers": [SERVER]})
    assert ookla.parse_servers(text) == [{"id": 1234, "name": "Example ISP", "location": "Austin, TX",
                                          "country": "United States", "host": "speedtest.example.net"}]
    assert ookla.parse_servers("garbage") == []


def test_packet_loss_rules():
    base = {"download_mbps": 300, "upload_mbps": 30, "latency_ms": 10, "jitter_ms": 1}
    assert not [r for r in advisor.speed_rules({**base, "packet_loss": 0}) if "loss" in r["title"].lower()]
    assert [r["severity"] for r in advisor.speed_rules({**base, "packet_loss": 1}) if "loss" in r["title"].lower()] == ["info"]
    assert [r["severity"] for r in advisor.speed_rules({**base, "packet_loss": 3}) if "loss" in r["title"].lower()] == ["warning"]
    assert [r["severity"] for r in advisor.speed_rules({**base, "packet_loss": 8}) if "loss" in r["title"].lower()] == ["critical"]


# --- end to end with a fake `speedtest` executable --------------------------------------

def install_fake_cli(tmp_path, monkeypatch, version="Speedtest by Ookla 1.2.0.84 (ea6b6773cf) Linux/x86_64", fail=False):
    script = tmp_path / "speedtest"
    lines = [json.dumps(d) for d in JSONL]
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, json
        if "--version" in sys.argv:
            print({version!r}); sys.exit(0)
        if "--servers" in sys.argv:
            print(json.dumps({{"servers": [{json.dumps(SERVER)}]}})); sys.exit(0)
        assert "--accept-license" in sys.argv and "--format=jsonl" in sys.argv
        if {fail!r}:
            print(json.dumps({{"type": "log", "level": "error", "message": "Configuration - Couldn't resolve host name"}}), file=sys.stderr)
            sys.exit(2)
        for line in {lines!r}:
            print(line, flush=True)
        """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ookla, "_cached", None)


pytestmark_unix = pytest.mark.skipif(sys.platform == "win32", reason="fake CLI is a shebang script")


async def collect(gen):
    return [ev async for ev in gen]


@pytestmark_unix
def test_status_and_auto_engine_use_ookla(tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch)
    st = asyncio.run(ookla.status())
    assert st["installed"] and st["version"].startswith("Speedtest by Ookla")
    assert asyncio.run(ookla.servers())[0]["id"] == 1234
    events = asyncio.run(collect(speedtest.run("auto")))
    assert events[0] == {"phase": "info", "type": "engine", "engine": "ookla", "label": "Speedtest.net (Ookla)"}
    assert events[-1]["phase"] == "done" and events[-1]["download_mbps"] == 296.0


@pytestmark_unix
def test_wrong_speedtest_tool_is_detected(tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, version="speedtest-cli 2.1.3")
    st = asyncio.run(ookla.status())
    assert not st["installed"] and "different tool" in st["conflict"]


@pytestmark_unix
def test_cli_errors_are_reported(tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, fail=True)
    events = asyncio.run(collect(ookla.run()))
    assert events[-1]["type"] == "error" and "resolve host name" in events[-1]["message"]


def test_auto_falls_back_to_cloudflare(monkeypatch):
    async def not_installed(refresh=False):
        return {"installed": False}

    async def fake_cf(**_):
        yield {"phase": "done", "type": "result", "download_mbps": 50.0, "upload_mbps": 5.0, "latency_ms": 20.0, "jitter_ms": 1.0}

    monkeypatch.setattr(ookla, "status", not_installed)
    monkeypatch.setattr(speedtest, "run_speedtest", fake_cf)
    events = asyncio.run(collect(speedtest.run("auto")))
    assert events[0]["engine"] == "cloudflare" and events[-1]["engine"] == "Cloudflare"
