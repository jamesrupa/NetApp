import asyncio

from fastapi.testclient import TestClient

from netapp import server
from netapp.tools import macos, wifiscan


class FakeChannel:
    def __init__(self, number, band):
        self.number, self.band = number, band

    def channelNumber(self):
        return self.number

    def channelBand(self):
        return self.band


class FakeNet:
    """Mimics the CWNetwork methods NetApp uses."""

    def __init__(self, ssid, bssid, rssi, channel, band, security_codes=(4,), noise=-92):
        self._ssid, self._bssid, self._rssi, self._noise = ssid, bssid, rssi, noise
        self._ch = FakeChannel(channel, band)
        self._sec = set(security_codes)

    def ssid(self):
        return self._ssid

    def bssid(self):
        return self._bssid

    def rssiValue(self):
        return self._rssi

    def noiseMeasurement(self):
        return self._noise

    def wlanChannel(self):
        return self._ch

    def supportsSecurity_(self, code):
        return code in self._sec


def test_corewlan_network_with_permission():
    n = macos.corewlan_network(FakeNet("HomeNet", "aa:bb:cc:dd:ee:01", -52, 149, 2, (11, 13, 4)), "AA:BB:CC:DD:EE:01", "HomeNet")
    assert n["ssid"] == "HomeNet" and n["bssid"] == "AA:BB:CC:DD:EE:01" and n["in_use"] and not n["redacted"]
    assert n["band"] == "5 GHz" and n["channel"] == 149 and n["signal_dbm"] == -52 and n["noise_dbm"] == -92
    assert n["security"] == "WPA3 Personal"
    other = macos.corewlan_network(FakeNet("Cafe", None, -80, 6, 1, (0,)), "AA:BB:CC:DD:EE:01", "HomeNet")
    assert other["security"] == "Open" and other["band"] == "2.4 GHz" and not other["in_use"]


def test_corewlan_network_without_permission():
    n = macos.corewlan_network(FakeNet(None, None, -60, 36, 2), None, None)
    assert n["redacted"] and n["ssid"] == "" and n["hidden"] and n["bssid"] is None and not n["in_use"]


def test_system_profiler_redacted_names():
    data = {"SPAirPortDataType": [{"spairport_airport_interfaces": [{
        "spairport_current_network_information": {"_name": "<redacted>", "spairport_network_channel": "149 (5GHz, 80MHz)",
                                                  "spairport_signal_noise": "-52 dBm / -94 dBm"},
        "spairport_airport_other_local_wireless_networks": [{"_name": "<redacted>", "spairport_network_channel": "6 (2GHz, 20MHz)"}],
    }]}]}
    nets = wifiscan.parse_system_profiler(data)
    assert all(n["redacted"] and n["ssid"] == "" for n in nets)
    assert nets[0]["in_use"] and nets[0]["signal_dbm"] == -52


def test_scan_wifi_on_mac_reports_location_status(monkeypatch):
    monkeypatch.setattr(wifiscan, "IS_LINUX", False)
    monkeypatch.setattr(wifiscan, "IS_MAC", True)
    monkeypatch.setattr(macos, "scan_corewlan", lambda: [macos.corewlan_network(FakeNet(None, None, -60, 36, 2), None, None)])
    monkeypatch.setattr(macos, "location_status", lambda: {"status": "not_determined", "how_to": "x", "settings_url": "y"})
    result = asyncio.run(wifiscan.scan_wifi())
    assert result["location"]["status"] == "not_determined" and result["networks"][0]["redacted"]

    monkeypatch.setattr(macos, "scan_corewlan", lambda: [macos.corewlan_network(FakeNet("Home", "aa:bb:cc:dd:ee:ff", -50, 36, 2), "aa:bb:cc:dd:ee:ff", "Home")])
    assert "location" not in asyncio.run(wifiscan.scan_wifi())


def test_scan_falls_back_to_system_profiler(monkeypatch):
    def broken():
        raise ImportError("no pyobjc")

    async def profiler():
        return [wifiscan.make_network("Home", channel=6, signal_dbm=-50, in_use=True)]

    monkeypatch.setattr(wifiscan, "IS_LINUX", False)
    monkeypatch.setattr(wifiscan, "IS_MAC", True)
    monkeypatch.setattr(macos, "scan_corewlan", broken)
    monkeypatch.setattr(wifiscan, "_scan_system_profiler", profiler)
    assert asyncio.run(wifiscan.scan_wifi())["networks"][0]["ssid"] == "Home"


def test_location_endpoints_without_corelocation():
    client = TestClient(server.app)
    assert client.get("/api/macos/location").json()["status"] == "unavailable"
    assert client.post("/api/macos/location/request").json()["status"] == "unavailable"


def test_is_redacted():
    assert macos.is_redacted("<redacted>") and macos.is_redacted(None) and macos.is_redacted("")
    assert not macos.is_redacted("HomeNet")
