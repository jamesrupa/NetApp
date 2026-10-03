"""Port reference: what common TCP/UDP ports are used for, and what to watch out for.

risk: "ok" (normal / encrypted), "caution" (fine on a trusted LAN, keep it off the
internet or lock it down), "risky" (unencrypted logins or a frequent attack target).
"""

from __future__ import annotations

# (port, protocol, name, category, encrypted, risk, description, note)
_PORTS = [
    # Web
    (80, "TCP", "HTTP", "Web", False, "caution", "Unencrypted web traffic; also most router, printer and camera admin pages.",
     "Anything sent, including passwords, is readable on the path. Prefer HTTPS (443)."),
    (443, "TCP", "HTTPS", "Web", True, "ok", "Encrypted web traffic (HTTP over TLS).", ""),
    (443, "UDP", "QUIC / HTTP/3", "Web", True, "ok", "Modern encrypted web transport used by Google, YouTube, Cloudflare and others.", ""),
    (8080, "TCP", "HTTP alternate", "Web", False, "caution", "Second web port: proxies, dev servers, admin consoles, IoT hubs.",
     "Often an admin page: check what it is and that it needs a password."),
    (8443, "TCP", "HTTPS alternate", "Web", True, "ok", "Second HTTPS port: admin consoles (UniFi, firewalls), app servers.", ""),
    (8000, "TCP", "HTTP alternate", "Web", False, "caution", "Development web servers, media servers, some cameras.", ""),
    (8888, "TCP", "HTTP alternate", "Web", False, "caution", "Development servers, Jupyter notebooks, proxies.",
     "A Jupyter notebook without a password lets anyone run code on the machine."),
    (3000, "TCP", "Dev web server", "Web", False, "caution", "Node.js/React/Grafana development servers.", ""),
    # Email
    (25, "TCP", "SMTP", "Email", False, "caution", "Mail servers sending email to each other.",
     "Home ISPs often block outbound 25 to stop spam from infected PCs."),
    (465, "TCP", "SMTPS", "Email", True, "ok", "Sending email from a mail app, encrypted from the start (implicit TLS).", ""),
    (587, "TCP", "SMTP submission", "Email", True, "ok", "Sending email from a mail app; upgrades to encryption with STARTTLS.", ""),
    (110, "TCP", "POP3", "Email", False, "risky", "Downloading email (old protocol).", "Unencrypted: the mailbox password is visible. Use 995."),
    (995, "TCP", "POP3S", "Email", True, "ok", "Downloading email over TLS.", ""),
    (143, "TCP", "IMAP", "Email", False, "risky", "Reading email that stays on the server.", "Unencrypted unless upgraded with STARTTLS. Prefer 993."),
    (993, "TCP", "IMAPS", "Email", True, "ok", "Reading email over TLS.", ""),
    # Remote access
    (22, "TCP", "SSH", "Remote access", True, "caution", "Encrypted remote terminal, file transfer (SFTP/SCP) and tunnels.",
     "Safe protocol, but exposed SSH is constantly password-guessed: use keys, not passwords."),
    (23, "TCP", "Telnet", "Remote access", False, "risky", "Old remote terminal.",
     "Everything, including the password, is sent in clear text. Disable it and use SSH."),
    (3389, "TCP", "RDP", "Remote access", True, "risky", "Windows Remote Desktop.",
     "One of the most attacked services on the internet. Never forward it from the router; use a VPN."),
    (5900, "TCP", "VNC", "Remote access", False, "risky", "Screen sharing (VNC, macOS Screen Sharing, Raspberry Pi).",
     "Often weak or no password and unencrypted. Keep it on the LAN or behind a VPN."),
    (5938, "TCP", "TeamViewer", "Remote access", True, "caution", "TeamViewer remote support.", "Make sure you know who has access."),
    (2222, "TCP", "SSH alternate", "Remote access", True, "caution", "SSH moved off port 22 to reduce noise from scanners.", ""),
    # File sharing
    (20, "TCP", "FTP data", "File sharing", False, "risky", "FTP's data connection.", "See port 21."),
    (21, "TCP", "FTP", "File sharing", False, "risky", "Classic file transfer.", "Logins and files are unencrypted. Use SFTP (22) or FTPS."),
    (69, "UDP", "TFTP", "File sharing", False, "caution", "Trivial file transfer: network boot and device firmware/config.", "No authentication at all."),
    (139, "TCP", "NetBIOS session", "File sharing", False, "caution", "Legacy Windows file sharing.", "Should never be reachable from the internet."),
    (445, "TCP", "SMB", "File sharing", False, "caution", "Windows/macOS/NAS file and printer sharing.",
     "Fine on a LAN, dangerous on the internet (WannaCry spread through it). Make sure SMBv1 is off."),
    (548, "TCP", "AFP", "File sharing", False, "caution", "Apple Filing Protocol: older Mac file sharing and Time Machine.", "Deprecated by Apple in favour of SMB."),
    (2049, "TCP/UDP", "NFS", "File sharing", False, "caution", "Unix/Linux network file system.", "Access control is by IP address only; keep it on trusted networks."),
    (873, "TCP", "rsync", "File sharing", False, "caution", "rsync daemon for backups and mirroring.", "Unauthenticated modules expose files."),
    (5000, "TCP", "Synology DSM / UPnP / dev", "File sharing", False, "caution", "Synology NAS web UI (HTTP), UPnP on routers, Flask and AirPlay on Macs.", ""),
    (5001, "TCP", "Synology DSM (HTTPS)", "File sharing", True, "caution", "Synology NAS web UI over HTTPS.", "Don't forward it to the internet; use QuickConnect or a VPN."),
    # Name & directory services
    (53, "TCP/UDP", "DNS", "Name services", False, "ok", "Turning names into IP addresses.",
     "Normal on routers. An open DNS resolver reachable from the internet can be abused for attacks."),
    (853, "TCP", "DNS over TLS", "Name services", True, "ok", "Encrypted DNS (DoT), e.g. Android Private DNS.", ""),
    (5353, "UDP", "mDNS / Bonjour", "Name services", False, "ok", "Devices announcing themselves on the LAN (AirPlay, printers, Chromecast).", ""),
    (5355, "UDP", "LLMNR", "Name services", False, "caution", "Windows local name lookup.", "Can be spoofed to steal Windows password hashes; disable on corporate networks."),
    (137, "UDP", "NetBIOS name", "Name services", False, "caution", "Legacy Windows name lookups.", ""),
    (138, "UDP", "NetBIOS datagram", "Name services", False, "caution", "Legacy Windows browsing announcements.", ""),
    (389, "TCP", "LDAP", "Name services", False, "caution", "Directory lookups (Active Directory, OpenLDAP).", "Unencrypted binds expose passwords; use 636 or StartTLS."),
    (636, "TCP", "LDAPS", "Name services", True, "ok", "LDAP over TLS.", ""),
    (88, "TCP/UDP", "Kerberos", "Name services", True, "ok", "Windows domain / Active Directory sign-in.", ""),
    # Network infrastructure
    (67, "UDP", "DHCP server", "Infrastructure", False, "ok", "The router handing out IP addresses.", "Two DHCP servers on one network cause conflicts."),
    (68, "UDP", "DHCP client", "Infrastructure", False, "ok", "Devices asking for an IP address.", ""),
    (123, "UDP", "NTP", "Infrastructure", False, "ok", "Clock synchronization.", ""),
    (161, "UDP", "SNMP", "Infrastructure", False, "caution", "Monitoring/managing switches, routers and printers.",
     "v1/v2c use a plain-text 'community' password (often 'public'). Use SNMPv3 or turn it off."),
    (162, "UDP", "SNMP trap", "Infrastructure", False, "caution", "Alerts sent by network devices.", ""),
    (514, "UDP", "Syslog", "Infrastructure", False, "ok", "Sending logs to a log server.", ""),
    (1900, "UDP", "SSDP / UPnP", "Infrastructure", False, "caution", "Device discovery for smart TVs, consoles, media devices.",
     "UPnP lets apps open router ports automatically; disable it on the router if you don't need it."),
    (5351, "UDP", "NAT-PMP / PCP", "Infrastructure", False, "caution", "Apple's automatic port mapping (like UPnP).", ""),
    (49152, "TCP", "UPnP (router)", "Infrastructure", False, "caution", "UPnP control on many routers.", ""),
    (179, "TCP", "BGP", "Infrastructure", False, "ok", "Routing between internet providers.", "You'd only see this on ISP/datacenter gear."),
    (520, "UDP", "RIP", "Infrastructure", False, "caution", "Old routing protocol.", ""),
    (7547, "TCP", "TR-069 / CWMP", "Infrastructure", False, "caution", "ISP remote management of your router/modem.",
     "Has been exploited at scale; it should only answer your ISP."),
    # VPN & security
    (500, "UDP", "IKE (IPsec)", "VPN", True, "ok", "Setting up IPsec VPN tunnels.", ""),
    (4500, "UDP", "IPsec NAT-T", "VPN", True, "ok", "IPsec VPN traffic through NAT.", ""),
    (1194, "UDP", "OpenVPN", "VPN", True, "ok", "OpenVPN tunnels.", ""),
    (51820, "UDP", "WireGuard", "VPN", True, "ok", "WireGuard VPN (also Tailscale, Mullvad and others).", ""),
    (1701, "UDP", "L2TP", "VPN", False, "caution", "L2TP VPN (normally wrapped in IPsec).", ""),
    (1723, "TCP", "PPTP", "VPN", False, "risky", "Old Microsoft VPN.", "PPTP's encryption is broken; replace it with WireGuard, OpenVPN or IPsec."),
    (41641, "UDP", "Tailscale", "VPN", True, "ok", "Tailscale direct connections (WireGuard-based).", ""),
    # Databases
    (1433, "TCP", "Microsoft SQL Server", "Databases", False, "risky", "SQL Server.", "Never expose to the internet; heavily brute-forced."),
    (3306, "TCP", "MySQL / MariaDB", "Databases", False, "risky", "MySQL/MariaDB database.", "Bind to localhost or a private network only."),
    (5432, "TCP", "PostgreSQL", "Databases", False, "caution", "PostgreSQL database.", "Bind to localhost or a private network only."),
    (6379, "TCP", "Redis", "Databases", False, "risky", "Redis in-memory database/cache.", "Often runs without a password: exposed Redis instances get hijacked quickly."),
    (27017, "TCP", "MongoDB", "Databases", False, "risky", "MongoDB database.", "Thousands of unauthenticated MongoDBs have been wiped and ransomed."),
    (9200, "TCP", "Elasticsearch", "Databases", False, "risky", "Elasticsearch search engine/API.", "Frequently found exposed with no authentication."),
    (11211, "UDP", "Memcached", "Databases", False, "risky", "Memcached cache.", "Exposed UDP memcached has been used for huge DDoS attacks."),
    # Messaging & IoT
    (1883, "TCP", "MQTT", "IoT & messaging", False, "caution", "IoT messaging (Home Assistant, sensors, smart plugs).", "Set a username/password on the broker."),
    (8883, "TCP", "MQTT over TLS", "IoT & messaging", True, "ok", "Encrypted MQTT.", ""),
    (5683, "UDP", "CoAP", "IoT & messaging", False, "caution", "Lightweight IoT protocol.", ""),
    (8123, "TCP", "Home Assistant", "IoT & messaging", False, "caution", "Home Assistant web UI.", "Use HTTPS and strong passwords if reachable remotely."),
    (5222, "TCP", "XMPP", "IoT & messaging", True, "ok", "Chat (XMPP/Jabber); also used by some IoT devices.", ""),
    (6667, "TCP", "IRC", "IoT & messaging", False, "caution", "Internet Relay Chat.", "Unexpected IRC traffic from a device can mean malware (botnets used IRC)."),
    # Media & devices
    (554, "TCP", "RTSP", "Media & devices", False, "caution", "Video streams from IP cameras and NVRs.", "Change the camera's default password; many streams are viewable without one."),
    (631, "TCP", "IPP / CUPS", "Media & devices", False, "ok", "Network printing (and the CUPS web UI on Macs/Linux).", ""),
    (9100, "TCP", "JetDirect / raw printing", "Media & devices", False, "caution", "Sending jobs straight to a printer.", "Anyone on the network can print; keep printers off the internet."),
    (515, "TCP", "LPD", "Media & devices", False, "caution", "Legacy line-printer protocol.", ""),
    (8008, "TCP", "Chromecast / Google Cast", "Media & devices", False, "ok", "Google Cast devices and smart TVs.", ""),
    (8009, "TCP", "Google Cast (TLS)", "Media & devices", True, "ok", "Google Cast control channel.", ""),
    (7000, "TCP", "AirPlay", "Media & devices", True, "ok", "AirPlay to Apple TV / HomePod / Macs.", ""),
    (32400, "TCP", "Plex", "Media & devices", True, "ok", "Plex Media Server.", ""),
    (8096, "TCP", "Jellyfin / Emby", "Media & devices", False, "caution", "Jellyfin/Emby media server.", ""),
    (62078, "TCP", "Apple iDevice sync", "Media & devices", True, "ok", "iPhone/iPad Wi-Fi sync (lockdownd).", "Its presence identifies Apple mobile devices."),
    (3689, "TCP", "DAAP (iTunes sharing)", "Media & devices", False, "ok", "Music library sharing.", ""),
    (9000, "TCP", "Various web UIs", "Media & devices", False, "caution", "Portainer, SonarQube, PHP-FPM, some NAS and media apps.", ""),
    # Gaming & apps
    (3074, "TCP/UDP", "Xbox Live", "Gaming & calls", False, "ok", "Xbox online play.", ""),
    (3478, "UDP", "STUN / TURN", "Gaming & calls", False, "ok", "Calls and games finding a path through NAT (also PlayStation, Zoom, WebRTC).", ""),
    (3479, "UDP", "PlayStation Network", "Gaming & calls", False, "ok", "PlayStation online play.", ""),
    (25565, "TCP", "Minecraft", "Gaming & calls", False, "caution", "Minecraft Java servers.", "Keep the server software updated if it's open to the internet."),
    (27015, "TCP/UDP", "Steam / Source games", "Gaming & calls", False, "ok", "Steam game servers.", ""),
    (19132, "UDP", "Minecraft Bedrock", "Gaming & calls", False, "ok", "Minecraft Bedrock servers.", ""),
    (8801, "UDP", "Zoom", "Gaming & calls", True, "ok", "Zoom meeting media.", ""),
    # Monitoring & management
    (9090, "TCP", "Prometheus / Cockpit", "Management", False, "caution", "Prometheus metrics UI or Cockpit (Linux admin web console).", ""),
    (10000, "TCP", "Webmin", "Management", False, "risky", "Webmin server admin panel.", "Full control of the server; never expose without strong auth and HTTPS."),
    (2375, "TCP", "Docker API", "Management", False, "risky", "Docker remote API without TLS.", "Gives root access to the host. Never expose it."),
    (2376, "TCP", "Docker API (TLS)", "Management", True, "caution", "Docker remote API over TLS.", ""),
    (6443, "TCP", "Kubernetes API", "Management", True, "caution", "Kubernetes control plane.", ""),
    (5985, "TCP", "WinRM (HTTP)", "Management", False, "caution", "Windows remote management / PowerShell remoting.", ""),
    (5986, "TCP", "WinRM (HTTPS)", "Management", True, "caution", "Windows remote management over HTTPS.", ""),
    (135, "TCP", "MS-RPC", "Management", False, "caution", "Windows RPC endpoint mapper.", "Should never be reachable from the internet."),
    (111, "TCP/UDP", "rpcbind", "Management", False, "caution", "Unix RPC port mapper (used by NFS).", ""),
]

FIELDS = ("port", "protocol", "name", "category", "encrypted", "risk", "description", "note")
PORTS = [dict(zip(FIELDS, p)) for p in sorted(_PORTS, key=lambda p: (p[0], p[1]))]
CATEGORIES = sorted({p["category"] for p in PORTS})
RANGES = [
    {"range": "0-1023", "name": "Well-known ports",
     "description": "Assigned to core services (web, email, DNS, SSH). On Linux/macOS only administrators can listen on them."},
    {"range": "1024-49151", "name": "Registered ports", "description": "Registered with IANA by applications (databases, games, media servers)."},
    {"range": "49152-65535", "name": "Dynamic / ephemeral ports",
     "description": "Picked at random by your computer for the client side of each connection, so they change every time."},
]


def search(query: str = "", category: str = "") -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    for p in PORTS:
        if category and p["category"] != category:
            continue
        if q:
            if q.isdigit():
                if str(p["port"]) != q:
                    continue
            elif q not in " ".join((p["name"], p["description"], p["note"], p["category"])).lower():
                continue
        out.append(p)
    return out


def describe(port: int) -> list[dict]:
    return [p for p in PORTS if p["port"] == port]
