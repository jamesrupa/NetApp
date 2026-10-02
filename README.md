# NetApp: Network Analysis & Diagnostic Toolkit

A local network toolkit you run on your own computer and use from your browser.
It is a small Python server (FastAPI) and a plain HTML/JS dashboard. The server
does the work a browser can't, like sending pings, opening sockets and asking the OS
for nearby Wi-Fi networks. The dashboard shows the results live.

| Tool | What it does |
|---|---|
| **Health Check** | One-click **Quick scan** (speed test) or **Full scan** (speed test, Wi-Fi signal and channel analysis, device discovery and an open-port security check). Ends with a health score and a prioritized list of recommended changes. Results export as HTML/JSON/CSV. |
| **Overview** | Hostname, local IP, default gateway, DNS servers, public IP and every network interface. |
| **Speed Test** | Latency (median ping) and jitter, then multi-stream download and upload throughput with a live chart. Uses Cloudflare's speed-test endpoints. |
| **Network Scanner** | Sweeps your LAN for devices using ICMP ping, TCP probes and the ARP table. Shows IP, hostname, MAC (flags private/randomized MACs) and response time. Can also run a common-ports scan on each device. |
| **Wi-Fi Scanner** | Nearby access points with SSID, BSSID, signal (dBm and quality), channel, band and security. Includes per-band channel congestion charts and a suggested least-crowded channel. |

## Quick start

Requires **Python 3.10+**.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m netapp
```

Your browser opens at <http://localhost:8765>. Options: `--port 9000` and `--no-browser`.
`--host 0.0.0.0` exposes the dashboard to other machines on your network, so use it with care.

You can also run `pip install -e .` to get a `netapp` command.

### Platform notes

| | Wi-Fi scanning | Network scanning |
|---|---|---|
| **Windows** | `netsh wlan`. On Windows 11 it needs **Location** access (Settings › Privacy & security › Location). The parser expects English output. | `ping`, `arp -a` |
| **macOS** | `system_profiler`. On macOS 14+ SSIDs are hidden unless your terminal/Python has **Location Services** permission. BSSIDs are not exposed. | `ping`, `arp -an` |
| **Linux** | `nmcli` (NetworkManager) | `ping`, `ip neigh` |

None of the tools need admin/root rights.

## Health Check: quick and full scans

| | Quick scan | Full scan |
|---|---|---|
| Speed test (download, upload, ping, jitter) | ✔ | ✔ |
| Wi-Fi signal, security, band and channel congestion | | ✔ |
| Device discovery on your LAN | | ✔ |
| Open-port security check of every device | | ✔ |
| Typical time | ~25 s | 1–3 min |

When a scan finishes you get:

- **A health score (0–100)**: each critical issue costs 25 points and each warning 10.
  Any critical issue caps the grade at "Fair".
- **Recommendations**, sorted Critical → Warning → Info → Looks good. Each one says what was found, why it
  matters, and the exact change to make. Examples: switching the router to a less crowded channel, moving off
  2.4 GHz, upgrading WPA/TKIP or open Wi-Fi, disabling Telnet/FTP/RDP/VNC on devices, enabling HTTPS for the
  router admin page, and reviewing UPnP.
- **Exports**: an HTML report (prints neatly to PDF), JSON, and CSVs of recommendations, devices and Wi-Fi networks.
  **Full scans are saved automatically** to `~/NetApp-Reports/`. Change the folder with `--reports-dir`.

The rules live in `netapp/tools/advisor.py`. Each rule is a small, plain function, so it's easy to tune the
thresholds or add new checks.

## How it works

```
netapp/
  __main__.py        # launcher: `python -m netapp`
  server.py          # FastAPI routes; long tasks stream Server-Sent Events
  system.py          # cross-platform command runner
  tools/
    netinfo.py       # interfaces, gateway, DNS, public IP
    speedtest.py     # latency/jitter, download, upload
    netscan.py       # host discovery, ARP, reverse DNS, port scan
    wifiscan.py      # per-OS Wi-Fi parsers + channel analysis
    diagnose.py      # quick/full scan orchestration
    advisor.py       # rules that turn results into recommendations + score
    report.py        # HTML / JSON / CSV export and auto-save
  static/            # index.html, styles.css, app.js (no build step)
tests/               # parser tests with sample OS output, speed-test & API tests
```

Some background on the techniques used:

- **Host discovery** combines three signals because many devices (phones especially) ignore ping.
  A TCP connection that is *refused* still proves a host is up, because it answered with a RST.
  Probing an address also makes your OS ARP for it, so any IP that ends up in the ARP table answered at layer 2.
- **Randomized MACs**: if the second-lowest bit of the first byte is set (the "locally administered" bit),
  the MAC was made up by the device for privacy, as modern phones and laptops do.
- **Speed test**: several parallel HTTP streams fill the pipe. The first second is ignored (TCP slow start),
  and the result is the average of 250 ms samples after that.
- **2.4 GHz channels** overlap; only 1, 6 and 11 don't. The recommendation scores each of those by how many
  nearby networks overlap it, weighted by their signal strength.

## Development

```bash
pip install -e ".[dev]"
pytest
```

### Adding a new tool

1. Write the logic in `netapp/tools/<tool>.py`. Keep it free of web code so it is easy to test.
2. Add a route in `server.py`. Return JSON for quick results, or `sse(async_generator)` for a live stream.
3. Add a tab button and a `<section id="tab-<name>">` in `static/index.html`, and its logic in `static/app.js`
   (add `loaders.<name>` if the tab should load data when opened).

### Ideas for next tools

- Ping / traceroute with a latency graph
- DNS lookup and DNS server benchmark
- MAC vendor lookup (IEEE OUI database)
- Continuous Wi-Fi signal monitor (for walking around to find dead zones)
- Scan history with trends over time (compare reports)
- Scheduled scans
- Subnet / CIDR calculator
- Service banner grabbing and mDNS/SSDP device discovery
- Packaging as a desktop app (e.g. pywebview or PyInstaller)

## Responsible use

Only scan networks you own or have permission to test. The scanner refuses to scan
public (non-private) address ranges. By default the server only listens on `localhost`.
