"""macOS-specific Wi-Fi support: Location Services permission and native CoreWLAN scans.

Since macOS 14 (Sonoma), Wi-Fi network names (SSIDs) and access-point IDs (BSSIDs)
are hidden ("<redacted>") from any app without Location Services permission,
because nearby networks reveal where you are. Python can ask for that permission
through CoreLocation; once granted, CoreWLAN returns the real names.

Everything here degrades gracefully: if pyobjc isn't installed or a call fails,
callers fall back to `system_profiler`.
"""

from __future__ import annotations

import threading
import time

REDACTED = {"<redacted>", "redacted", ""}

# CLAuthorizationStatus values
STATUS_NAMES = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized", 4: "authorized"}

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices"
HOW_TO = ("Open System Settings › Privacy & Security › Location Services, make sure Location Services is on, "
          "and switch on \"NetApp Wi-Fi Helper\". Then scan again.")

# CWSecurity enum (CoreWLAN), checked strongest first.
CW_SECURITY = [
    (11, "WPA3 Personal"), (12, "WPA3 Enterprise"), (13, "WPA2/WPA3 Personal"),
    (14, "OWE"), (15, "OWE Transition"),
    (4, "WPA2 Personal"), (9, "WPA2 Enterprise"), (5, "WPA/WPA2 Personal"), (10, "WPA/WPA2 Enterprise"),
    (3, "WPA/WPA2 Personal"), (8, "WPA/WPA2 Enterprise"), (2, "WPA Personal"), (7, "WPA Enterprise"),
    (6, "Dynamic WEP"), (1, "WEP"), (0, "Open"),
]
CW_BAND = {1: "2.4 GHz", 2: "5 GHz", 3: "6 GHz"}


def is_redacted(ssid) -> bool:
    return ssid is None or str(ssid).strip().lower() in REDACTED


# --- Location Services ----------------------------------------------------------------

def _status_code(manager=None) -> int | None:
    import CoreLocation  # type: ignore[import-not-found]

    if manager is not None and hasattr(manager, "authorizationStatus"):
        try:
            return int(manager.authorizationStatus())
        except Exception:
            pass
    return int(CoreLocation.CLLocationManager.authorizationStatus())


def location_status() -> dict:
    """Location permission of the NetApp Wi-Fi Helper app (falls back to this Python process)."""
    helper_error = None
    if _on_mac():
        from . import macos_helper
        try:
            return {**macos_helper.status(), "via": "helper", "how_to": HOW_TO, "settings_url": SETTINGS_URL}
        except macos_helper.HelperUnavailable as exc:
            helper_error = str(exc)
    result = _python_location_status()
    if helper_error:
        result["helper_error"] = helper_error
    return result


def _on_mac() -> bool:
    from ..system import IS_MAC
    return IS_MAC


def _python_location_status() -> dict:
    try:
        import CoreLocation  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return {"status": "unavailable", "services_enabled": None, "how_to": HOW_TO, "settings_url": SETTINGS_URL,
                "detail": "pyobjc-framework-CoreLocation is not installed. Run `pip install -r requirements.txt`."}
    import CoreLocation  # type: ignore[import-not-found]

    try:
        enabled = bool(CoreLocation.CLLocationManager.locationServicesEnabled())
        code = _status_code()
    except Exception as exc:  # pragma: no cover - depends on the OS
        return {"status": "unavailable", "detail": str(exc), "how_to": HOW_TO, "settings_url": SETTINGS_URL}
    return {"status": STATUS_NAMES.get(code, "unknown"), "services_enabled": enabled,
            "how_to": HOW_TO, "settings_url": SETTINGS_URL}


_request_lock = threading.Lock()


def request_location(timeout: float = 30.0) -> dict:
    """Ask macOS for Location access: through the helper app if possible, else for Python itself."""
    if _on_mac():
        from . import macos_helper
        try:
            return {**macos_helper.request(), "via": "helper", "how_to": HOW_TO, "settings_url": SETTINGS_URL}
        except macos_helper.HelperUnavailable as exc:
            return {**_python_request_location(timeout), "helper_error": str(exc)}
    return _python_request_location(timeout)


def _python_request_location(timeout: float = 30.0) -> dict:
    """Ask macOS for Location access for this Python process and wait (spinning a run loop) for the answer.

    macOS shows a prompt the first time; afterwards the answer is remembered and can
    only be changed in System Settings. Either way "Python" shows up in the list there.
    """
    try:
        import CoreLocation  # type: ignore[import-not-found]
        import Foundation  # type: ignore[import-not-found]
    except ImportError:
        return _python_location_status()
    with _request_lock:
        manager = CoreLocation.CLLocationManager.alloc().init()
        code = _status_code(manager)
        if code in (0, None):
            if hasattr(manager, "requestWhenInUseAuthorization"):
                manager.requestWhenInUseAuthorization()
            manager.startUpdatingLocation()  # also triggers the request on older macOS
            deadline = time.time() + timeout
            while time.time() < deadline:
                Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.5))
                code = _status_code(manager)
                if code not in (0, None):
                    break
            manager.stopUpdatingLocation()
    return _python_location_status()


# --- CoreWLAN scan ------------------------------------------------------------------

def _security(net) -> str | None:
    for code, label in CW_SECURITY:
        try:
            if net.supportsSecurity_(code):
                return label
        except Exception:
            return None
    return None


def corewlan_network(net, current_bssid: str | None, current_ssid: str | None) -> dict:
    """Convert a CWNetwork (or anything with the same methods) into NetApp's network dict."""
    from .wifiscan import make_network

    ssid = net.ssid()
    bssid = net.bssid()
    ch = net.wlanChannel()
    redacted = is_redacted(ssid)
    if bssid and current_bssid:
        in_use = bssid.lower() == current_bssid.lower()
    else:
        in_use = bool(not redacted and current_ssid and ssid == current_ssid)
    return make_network(
        None if redacted else str(ssid),
        str(bssid) if bssid else None,
        signal_dbm=int(net.rssiValue()),
        channel=int(ch.channelNumber()) if ch else None,
        band=CW_BAND.get(int(ch.channelBand())) if ch else None,
        security=_security(net),
        in_use=in_use,
        noise_dbm=int(net.noiseMeasurement()) or None,
        redacted=redacted,
    )


def scan_corewlan() -> list[dict]:
    """Blocking scan through CoreWLAN (takes a few seconds). Raises ImportError if pyobjc is missing."""
    from CoreWLAN import CWWiFiClient  # type: ignore[import-not-found]

    iface = CWWiFiClient.sharedWiFiClient().interface()
    if iface is None:
        raise RuntimeError("No Wi-Fi interface found.")
    nets, err = iface.scanForNetworksWithName_error_(None)
    if err is not None or nets is None:
        raise RuntimeError(f"Wi-Fi scan failed: {err}")
    current_bssid, current_ssid = iface.bssid(), iface.ssid()
    out = [corewlan_network(n, current_bssid, current_ssid) for n in nets]
    # The connected network isn't always in the scan results; add it from the interface itself.
    if (current_bssid or current_ssid) and not any(n["in_use"] for n in out) and iface.rssiValue():
        from .wifiscan import make_network
        ch = iface.wlanChannel()
        out.append(make_network(
            None if is_redacted(current_ssid) else str(current_ssid), str(current_bssid) if current_bssid else None,
            signal_dbm=int(iface.rssiValue()),
            channel=int(ch.channelNumber()) if ch else None,
            band=CW_BAND.get(int(ch.channelBand())) if ch else None,
            in_use=True, noise_dbm=int(iface.noiseMeasurement()) or None, redacted=is_redacted(current_ssid),
        ))
    return out
