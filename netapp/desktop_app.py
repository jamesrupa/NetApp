"""NetApp as a desktop app: a native window (pywebview) and an installable launcher.

  python -m netapp --app            open NetApp in its own window
  python -m netapp --install-app    add NetApp to Applications (macOS), the Start menu + Desktop
                                    (Windows) or the app menu (Linux), with the NetApp icon
  python -m netapp --uninstall-app  remove that launcher again

The launcher runs this same Python environment, so it always uses your current copy of NetApp.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
ASSETS = PACKAGE_DIR / "desktop"
APP_ID = "com.netapp.desktop"

WEBVIEW_HELP = ("The desktop window needs pywebview: run `pip install -r requirements.txt` "
                "(on Linux also install GTK/WebKit, e.g. `sudo apt install python3-gi gir1.2-webkit2-4.1`, "
                "then `pip install pywebview`).")


# --- window ----------------------------------------------------------------------------

def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, preferred: int, attempts: int = 10) -> int:
    """The preferred port (keeps saved settings like the theme), or the next free one."""
    for port in range(preferred, preferred + attempts):
        if _port_free(host, port):
            return port
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def wait_for_server(host: str, port: int, timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _brand_macos_process() -> None:
    """Show NetApp's name and icon in the Dock/menu bar instead of Python's (best effort)."""
    try:
        from AppKit import NSApplication, NSImage  # type: ignore[import-not-found]
        from Foundation import NSBundle  # type: ignore[import-not-found]

        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "NetApp"
        icon = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "NetApp.icns"))
        if icon is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(icon)
    except Exception:
        pass


def run_window(host: str = "127.0.0.1", port: int = 8765) -> bool:
    """Run the server in the background and show it in a native window. False if pywebview is missing."""
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        return False
    import uvicorn

    port = pick_port(host, port)
    config = uvicorn.Config("netapp.server:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="netapp-server", daemon=True)
    thread.start()
    if not wait_for_server(host, port):
        print("NetApp's server didn't start; see the messages above.", file=sys.stderr)
        server.should_exit = True
        return True

    if sys.platform == "darwin":
        _brand_macos_process()
    for key, value in (("ALLOW_DOWNLOADS", True), ("OPEN_EXTERNAL_LINKS_IN_BROWSER", True)):
        try:
            webview.settings[key] = value  # pywebview 5+: report exports save normally, links open in your browser
        except (AttributeError, TypeError):
            pass
    webview.create_window("NetApp", f"http://{host}:{port}/", width=1400, height=900, min_size=(900, 600),
                          background_color="#060a12", text_select=True)
    # Keep browser storage between runs (theme choice etc.); pywebview defaults to a private session.
    storage = Path.home() / (".netapp" if sys.platform != "darwin" else "Library/Application Support/NetApp") / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    options = {"private_mode": False, "storage_path": str(storage), "icon": str(ASSETS / "NetApp.png")}
    while True:  # older pywebview versions lack some options: drop them one by one
        try:
            webview.start(**options)
            break
        except TypeError as exc:
            bad = next((k for k in options if k in str(exc)), None)
            if bad is None:
                raise
            options.pop(bad)
    server.should_exit = True
    thread.join(timeout=5)
    return True


# --- launcher install ------------------------------------------------------------------

def _python_for_launcher() -> str:
    """The interpreter of this environment (pythonw on Windows so no console window appears)."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def macos_app_path(apps_dir: Path | None = None) -> Path:
    return (apps_dir or Path.home() / "Applications") / "NetApp.app"


def build_macos_app(apps_dir: Path | None = None, python: str | None = None) -> Path:
    """A minimal .app bundle whose executable starts NetApp's desktop window."""
    app = macos_app_path(apps_dir)
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    shutil.copy(ASSETS / "NetApp.icns", app / "Contents" / "Resources" / "NetApp.icns")
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": "NetApp",
            "CFBundleDisplayName": "NetApp",
            "CFBundleIdentifier": APP_ID,
            "CFBundleExecutable": "NetApp",
            "CFBundleIconFile": "NetApp",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": _version(),
            "CFBundleVersion": _version(),
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        }, f)
    log = Path.home() / "Library" / "Logs" / "NetApp.log"
    launcher = app / "Contents" / "MacOS" / "NetApp"
    launcher.write_text(
        "#!/bin/bash\n"
        "# Starts NetApp's desktop window with the Python environment it was installed from.\n"
        f'mkdir -p "{log.parent}"\n'
        f'cd "{PROJECT_DIR}" || exit 1\n'
        f'exec "{python or _python_for_launcher()}" -m netapp --app >>"{log}" 2>&1\n'
    )
    launcher.chmod(0o755)
    return app


def _version() -> str:
    from . import __version__
    return __version__


def install_launcher() -> str:
    if sys.platform == "darwin":
        app = build_macos_app()
        lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                      "LaunchServices.framework/Support/lsregister")
        if os.path.exists(lsregister):  # refresh Finder/Dock so the icon shows straight away
            subprocess.run([lsregister, "-f", str(app)], capture_output=True)
        return (f"Installed {app}\nOpen it from Launchpad, Spotlight (\"NetApp\") or Finder › Applications, "
                "and drag it to the Dock to keep it there.")
    if sys.platform == "win32":
        paths = []
        for folder in (Path(os.environ["USERPROFILE"]) / "Desktop",
                       Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"):
            lnk = folder / "NetApp.lnk"
            script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
                      f"$s.TargetPath='{_python_for_launcher()}';$s.Arguments='-m netapp --app';"
                      f"$s.WorkingDirectory='{PROJECT_DIR}';$s.IconLocation='{ASSETS / 'NetApp.ico'}';"
                      "$s.Description='NetApp network diagnostics';$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
            paths.append(str(lnk))
        return "Created shortcuts:\n  " + "\n  ".join(paths)
    desktop = linux_desktop_entry()
    return f"Installed {desktop}\nNetApp now appears in your applications menu."


def linux_desktop_entry(apps_dir: Path | None = None, python: str | None = None) -> Path:
    apps = apps_dir or Path.home() / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    path = apps / "netapp.desktop"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=NetApp\nComment=Network analysis & diagnostics\n"
        f'Exec=sh -c \'cd "{PROJECT_DIR}" && exec "{python or _python_for_launcher()}" -m netapp --app\'\n'
        f"Icon={ASSETS / 'NetApp.png'}\nTerminal=false\nCategories=Network;Utility;\n"
    )
    return path


def uninstall_launcher() -> str:
    removed = []
    targets = [macos_app_path(), Path.home() / ".local" / "share" / "applications" / "netapp.desktop"]
    if sys.platform == "win32":
        targets += [Path(os.environ["USERPROFILE"]) / "Desktop" / "NetApp.lnk",
                    Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "NetApp.lnk"]
    for t in targets:
        if t.is_dir():
            shutil.rmtree(t)
            removed.append(str(t))
        elif t.exists():
            t.unlink()
            removed.append(str(t))
    return "Removed:\n  " + "\n  ".join(removed) if removed else "Nothing to remove."
