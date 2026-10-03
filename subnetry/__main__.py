"""Launch the Subnetry dashboard: `python -m subnetry` (or the `subnetry` command)."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Subnetry needs Python 3.10 or newer (this is {sys.version.split()[0]}). "
                 "On macOS: `brew install python` or download it from python.org.")
    import uvicorn

    from .system import raise_open_file_limit

    raise_open_file_limit()  # macOS allows only 256 open sockets by default; scans need more

    parser = argparse.ArgumentParser(prog="subnetry", description="Network analysis & diagnostic toolkit")
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--reports-dir", help="where full-scan reports are saved (default: ~/Subnetry-Reports)")
    parser.add_argument("--app", action="store_true", help="open Subnetry in its own desktop window")
    parser.add_argument("--install-app", action="store_true",
                        help="add a Subnetry launcher (macOS Applications / Windows Start menu / Linux app menu)")
    parser.add_argument("--uninstall-app", action="store_true", help="remove that launcher")
    args = parser.parse_args()
    if args.reports_dir:
        os.environ["SUBNETRY_REPORTS_DIR"] = os.path.abspath(args.reports_dir)

    from . import desktop_app

    if args.install_app:
        print(desktop_app.install_launcher())
        return
    if args.uninstall_app:
        print(desktop_app.uninstall_launcher())
        return
    if args.app:
        if desktop_app.run_window(args.host, args.port):
            return
        print(desktop_app.WEBVIEW_HELP, "Opening Subnetry in your browser instead.", file=sys.stderr)

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"Subnetry running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("subnetry.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
