import pytest

from netapp.tools import netinfo, netscan, wifiscan

NMCLI = (
    "*:AA\\:BB\\:CC\\:DD\\:EE\\:01:HomeNet:6:2437 MHz:270 Mbit/s:82:WPA2\n"
    " :AA\\:BB\\:CC\\:DD\\:EE\\:02:Cafe\\:Guest:36:5180 MHz:540 Mbit/s:40:\n"
    " :AA\\:BB\\:CC\\:DD\\:EE\\:03::1:2412 MHz:130 Mbit/s:30:WPA1 WPA2\n"
)

NETSH = """
Interface name : Wi-Fi
There are 2 networks currently visible.

SSID 1 : HomeNet
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:01
         Signal             : 90%
         Radio type         : 802.11ax
         Band               : 2.4 GHz
         Channel            : 6
    BSSID 2                 : aa:bb:cc:dd:ee:02
         Signal             : 70%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 44

SSID 2 : Neighbor
    Network type            : Infrastructure
    Authentication          : Open
    Encryption              : None
    BSSID 1                 : 11:22:33:44:55:66
         Signal             : 20%
         Channel            : 11
"""

SYSTEM_PROFILER = {
    "SPAirPortDataType": [{
        "spairport_airport_interfaces": [{
            "_name": "en0",
            "spairport_current_network_information": {
                "_name": "HomeNet",
                "spairport_network_channel": "149 (5GHz, 80MHz)",
                "spairport_security_mode": "spairport_security_mode_wpa2_personal",
                "spairport_signal_noise": "-52 dBm / -94 dBm",
            },
            "spairport_airport_other_local_wireless_networks": [
                {"_name": "Neighbor", "spairport_network_channel": "1 (2GHz, 20MHz)",
                 "spairport_security_mode": "spairport_security_mode_none",
                 "spairport_signal_noise": "-78 dBm / -94 dBm"},
                {"_name": "SixE", "spairport_network_channel": "37 (6GHz, 160MHz)",
                 "spairport_security_mode": "spairport_security_mode_wpa3_personal"},
            ],
        }],
    }],
}


def test_parse_nmcli():
    nets = wifiscan.parse_nmcli(NMCLI)
    assert len(nets) == 3
    home, cafe, hidden = nets
    assert home["bssid"] == "AA:BB:CC:DD:EE:01" and home["ssid"] == "HomeNet"
    assert home["in_use"] and home["band"] == "2.4 GHz" and home["signal_dbm"] == -59
    assert cafe["ssid"] == "Cafe:Guest" and cafe["band"] == "5 GHz" and cafe["security"] == "Open"
    assert hidden["hidden"] and hidden["channel"] == 1


def test_parse_netsh():
    nets = wifiscan.parse_netsh_networks(NETSH, connected_bssid="AA:BB:CC:DD:EE:01")
    assert [n["ssid"] for n in nets] == ["HomeNet", "HomeNet", "Neighbor"]
    assert nets[0]["in_use"] and not nets[1]["in_use"]
    assert nets[0]["security"] == "WPA2-Personal / CCMP"
    assert nets[1]["band"] == "5 GHz" and nets[1]["frequency_mhz"] == 5220
    assert nets[2]["band"] == "2.4 GHz" and nets[2]["security"] == "Open" and nets[2]["signal_percent"] == 20


def test_parse_system_profiler():
    nets = wifiscan.parse_system_profiler(SYSTEM_PROFILER)
    assert [n["ssid"] for n in nets] == ["HomeNet", "Neighbor", "SixE"]
    assert nets[0]["in_use"] and nets[0]["band"] == "5 GHz" and nets[0]["signal_dbm"] == -52
    assert nets[0]["noise_dbm"] == -94 and nets[0]["security"] == "WPA2 Personal"
    assert nets[1]["security"] == "Open" and nets[1]["band"] == "2.4 GHz"
    assert nets[2]["band"] == "6 GHz" and nets[2]["signal_dbm"] is None


def test_channel_report_prefers_quiet_channel():
    nets = [wifiscan.make_network(f"n{i}", channel=ch, signal_percent=80) for i, ch in enumerate([1, 1, 2, 6, 44, 36])]
    report = wifiscan.channel_report(nets)
    assert report["usage"]["2.4 GHz"] == {1: 2, 2: 1, 6: 1}
    assert report["recommendations"]["2.4 GHz"]["channel"] == 11
    assert report["recommendations"]["5 GHz"]["channel"] == 40


def test_parse_arp_tables():
    linux = (
        "192.168.1.1 dev wlan0 lladdr a4:2b:b0:11:22:33 REACHABLE\n"
        "192.168.1.50 dev wlan0  FAILED\n"
        "192.168.1.60 dev wlan0 lladdr 0a:00:00:00:00:01 STALE\n"
    )
    assert netscan.parse_arp_table(linux) == {"192.168.1.1": "A4:2B:B0:11:22:33", "192.168.1.60": "0A:00:00:00:00:01"}
    mac = "? (192.168.1.1) at a4:2b:b0:1:2:3 on en0 ifscope [ethernet]\n? (192.168.1.9) at (incomplete) on en0\n"
    assert netscan.parse_arp_table(mac) == {"192.168.1.1": "A4:2B:B0:01:02:03"}
    win = "  192.168.1.1           a4-2b-b0-11-22-33     dynamic\n  192.168.1.255         ff-ff-ff-ff-ff-ff     static\n"
    assert netscan.parse_arp_table(win) == {"192.168.1.1": "A4:2B:B0:11:22:33"}


def test_randomized_mac():
    assert netscan.is_randomized_mac("0A:00:00:00:00:01")
    assert not netscan.is_randomized_mac("A4:2B:B0:11:22:33")


@pytest.mark.parametrize("output,code,expected", [
    ("64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=3.42 ms", 0, 3.42),
    ("Reply from 192.168.1.1: bytes=32 time<1ms TTL=64", 0, 1.0),
    ("Reply from 192.168.1.20: Destination host unreachable.", 0, None),
    ("1 packets transmitted, 0 received", 1, None),
])
def test_parse_ping(output, code, expected):
    assert netscan.parse_ping(output, code) == expected


@pytest.mark.parametrize("cidr", ["8.8.8.0/24", "10.0.0.0/16", "not-a-network"])
def test_resolve_target_rejects(cidr):
    with pytest.raises(netscan.ScanError):
        netscan.resolve_target(cidr)


def test_resolve_target_accepts_lan():
    assert str(netscan.resolve_target("192.168.1.77/24")) == "192.168.1.0/24"


def test_gateway_parsers():
    proc = "Iface\tDestination\tGateway\tFlags\nwlan0\t00000000\t0101A8C0\t0003\n"
    assert netinfo.parse_proc_net_route(proc) == "192.168.1.1"
    win = "Network Destination        Netmask          Gateway       Interface  Metric\n          0.0.0.0          0.0.0.0      192.168.0.1    192.168.0.23     25\n"
    assert netinfo.parse_windows_route_print(win) == "192.168.0.1"


def test_ipconfig_dns():
    text = (
        "   DNS Servers . . . . . . . . . . . : 192.168.1.1\n"
        "                                       1.1.1.1\n"
        "   NetBIOS over Tcpip. . . . . . . . : Enabled\n"
    )
    assert netinfo.parse_ipconfig_dns(text) == ["192.168.1.1", "1.1.1.1"]
