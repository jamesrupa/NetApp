"""Render the NetApp icon SVG to PNGs and pack them into .icns (macOS) and .ico (Windows).

Developer tool, not needed to run NetApp: the generated files are committed.
Requires Playwright with Chromium:  python scripts/build_icons.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "netapp" / "static" / "brand" / "icon.svg"
OUT = ROOT / "netapp" / "desktop"
STATIC = ROOT / "netapp" / "static" / "brand"

# ICNS entry types holding PNG data, by pixel size (macOS picks the right one for each display).
ICNS_TYPES = [(b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
              (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512)]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(sizes: set[int]) -> dict[int, bytes]:
    from playwright.sync_api import sync_playwright

    svg = SVG.read_text()
    out = {}
    with sync_playwright() as p:
        kwargs = {"executable_path": sys.argv[1]} if len(sys.argv) > 1 else {}
        browser = p.chromium.launch(**kwargs)
        for size in sorted(sizes):
            page = browser.new_page(viewport={"width": size, "height": size})
            sized = svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)
            page.set_content(f"<html><body style='margin:0;background:transparent'>{sized}</body></html>")
            out[size] = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
            page.close()
        browser.close()
    return out


def build_icns(pngs: dict[int, bytes]) -> bytes:
    body = b"".join(kind + struct.pack(">I", len(pngs[size]) + 8) + pngs[size] for kind, size in ICNS_TYPES)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def build_ico(pngs: dict[int, bytes]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(ICO_SIZES))
    offset = len(header) + 16 * len(ICO_SIZES)
    entries, blobs = b"", b""
    for size in ICO_SIZES:
        data = pngs[size]
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def main() -> None:
    sizes = {s for _, s in ICNS_TYPES} | set(ICO_SIZES) | {180, 192}
    pngs = render(sizes)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "NetApp.icns").write_bytes(build_icns(pngs))
    (OUT / "NetApp.ico").write_bytes(build_ico(pngs))
    (OUT / "NetApp.png").write_bytes(pngs[512])
    (STATIC / "icon-192.png").write_bytes(pngs[192])
    (STATIC / "apple-touch-icon.png").write_bytes(pngs[180])
    (STATIC / "favicon-32.png").write_bytes(pngs[32])
    print("wrote", ", ".join(str(p.relative_to(ROOT)) for p in [OUT / "NetApp.icns", OUT / "NetApp.ico", OUT / "NetApp.png"]))


if __name__ == "__main__":
    main()
