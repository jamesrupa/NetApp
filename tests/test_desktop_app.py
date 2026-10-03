import plistlib
import sys
import types
import urllib.request

from subnetry import desktop_app


def test_macos_bundle(tmp_path):
    app = desktop_app.build_macos_app(tmp_path, python="/venv/bin/python")
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Subnetry" and info["CFBundleIconFile"] == "Subnetry"
    assert (app / "Contents" / "Resources" / "Subnetry.icns").read_bytes()[:4] == b"icns"
    launcher = app / "Contents" / "MacOS" / "Subnetry"
    text = launcher.read_text()
    assert launcher.stat().st_mode & 0o111 and '"/venv/bin/python" -m subnetry --app' in text
    assert f'cd "{desktop_app.PROJECT_DIR}"' in text
    desktop_app.build_macos_app(tmp_path, python="/venv/bin/python")  # reinstall replaces cleanly


def test_linux_desktop_entry(tmp_path):
    entry = desktop_app.linux_desktop_entry(tmp_path, python="/venv/bin/python")
    text = entry.read_text()
    assert "Name=Subnetry" in text and "-m subnetry --app" in text and "Subnetry.png" in text


def test_icons_present():
    assert (desktop_app.ASSETS / "Subnetry.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert (desktop_app.ASSETS / "Subnetry.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_pick_port_skips_busy_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        assert desktop_app.pick_port("127.0.0.1", busy) != busy


def test_run_window_serves_app_and_shuts_down(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = {}
    fake = types.ModuleType("webview")
    fake.settings = {}

    def create_window(title, url, **kw):
        seen["title"], seen["url"] = title, url

    def start(**options):
        if "icon" in options:  # simulate an older pywebview without the icon option
            raise TypeError("start() got an unexpected keyword argument 'icon'")
        seen["options"] = options
        seen["page"] = urllib.request.urlopen(seen["url"], timeout=5).read().decode()

    fake.create_window, fake.start = create_window, start
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert desktop_app.run_window("127.0.0.1", 18765)
    assert seen["title"] == "Subnetry" and "<title>Subnetry</title>" in seen["page"]
    assert seen["options"]["private_mode"] is False and fake.settings["ALLOW_DOWNLOADS"] is True


def test_run_window_without_pywebview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)  # import fails
    assert desktop_app.run_window() is False


def test_install_replaces_launcher_from_old_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    old = desktop_app.build_macos_app(tmp_path / "Applications", python="/venv/bin/python")
    # Make it look like the launcher an older version (named NetApp) installed.
    legacy = old.with_name("NetApp.app")
    old.rename(legacy)
    info = plistlib.loads((legacy / "Contents" / "Info.plist").read_bytes())
    info["CFBundleIdentifier"] = "com.netapp.desktop"
    (legacy / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    unrelated = tmp_path / "Applications" / "Other.app"
    unrelated.mkdir()
    assert desktop_app.remove_legacy_launchers() == [str(legacy)]
    assert not legacy.exists() and unrelated.exists()
