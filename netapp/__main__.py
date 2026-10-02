"""Launch the NetApp dashboard: `python -m netapp` (or the `netapp` command)."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"NetApp needs Python 3.10 or newer (this is {sys.version.split()[0]}). "
                 "On macOS: `brew install python` or download it from python.org.")
    import uvicorn

    from .system import raise_open_file_limit

    raise_open_file_limit()  # macOS allows only 256 open sockets by default; scans need more

    parser = argparse.ArgumentParser(prog="netapp", description="Network analysis & diagnostic toolkit")
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--reports-dir", help="where full-scan reports are saved (default: ~/NetApp-Reports)")
    args = parser.parse_args()
    if args.reports_dir:
        os.environ["NETAPP_REPORTS_DIR"] = os.path.abspath(args.reports_dir)

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"NetApp running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run("netapp.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
