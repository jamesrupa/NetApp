// Subnetry Wi-Fi Helper: a tiny macOS app that reads Wi-Fi details for Subnetry.
//
// macOS (14+) only reveals Wi-Fi network names (SSIDs) and access-point IDs (BSSIDs)
// to a proper app bundle that has Location Services permission. A plain command-line
// Python process can't keep that permission, so Subnetry builds this helper on the Mac,
// launches it with `open`, and reads the JSON it writes.
//
// Usage (arguments after `open -a "Subnetry Wi-Fi Helper.app" --args`):
//   status  <out.json>                         authorization status only
//   auth    <out.json>                         ask for Location access (shows the system prompt)
//   scan    <out.json>                         nearby networks + current connection
//   current <out.json>                         current connection only
//   monitor <out.jsonl> <seconds> <stopfile> <parentPid>
//                                              append one current-connection line per interval
//                                              until the stop file appears or the parent exits

import AppKit
import CoreLocation
import CoreWLAN
import Foundation

final class LocationAuth: NSObject, CLLocationManagerDelegate {
    let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
    }

    var status: Int { Int(manager.authorizationStatus.rawValue) }

    var statusName: String {
        switch status {
        case 0: return "not_determined"
        case 1: return "restricted"
        case 2: return "denied"
        case 3, 4: return "authorized"
        default: return "unknown"
        }
    }

    /// Ask for permission if it hasn't been decided yet, and wait for the answer.
    func request(timeout: TimeInterval) {
        guard status == 0 else { return }
        manager.requestWhenInUseAuthorization()
        let deadline = Date().addingTimeInterval(timeout)
        var startedUpdates = false
        while status == 0 && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.25))
            if !startedUpdates && Date() > deadline.addingTimeInterval(-timeout + 2) {
                manager.startUpdatingLocation()  // some macOS versions only prompt once location is used
                startedUpdates = true
            }
        }
        if startedUpdates { manager.stopUpdatingLocation() }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {}
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {}
    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {}
}

// CWSecurity raw values, strongest first.
let securityLabels: [(Int, String)] = [
    (11, "WPA3 Personal"), (12, "WPA3 Enterprise"), (13, "WPA2/WPA3 Personal"),
    (14, "OWE"), (15, "OWE Transition"),
    (4, "WPA2 Personal"), (9, "WPA2 Enterprise"), (5, "WPA/WPA2 Personal"), (10, "WPA/WPA2 Enterprise"),
    (3, "WPA/WPA2 Personal"), (8, "WPA/WPA2 Enterprise"), (2, "WPA Personal"), (7, "WPA Enterprise"),
    (6, "Dynamic WEP"), (1, "WEP"), (0, "Open"),
]

func securityLabel(_ network: CWNetwork) -> String? {
    for (raw, label) in securityLabels {
        if let sec = CWSecurity(rawValue: raw), network.supportsSecurity(sec) {
            return label
        }
    }
    return nil
}

func channelInfo(_ channel: CWChannel?) -> [String: Any] {
    guard let ch = channel else { return [:] }
    return ["channel": ch.channelNumber, "band": ch.channelBand.rawValue, "width": ch.channelWidth.rawValue]
}

func networkInfo(_ n: CWNetwork, currentBSSID: String?) -> [String: Any] {
    var d: [String: Any] = ["rssi": n.rssiValue, "noise": n.noiseMeasurement]
    d.merge(channelInfo(n.wlanChannel)) { $1 }
    if let s = n.ssid { d["ssid"] = s }
    if let b = n.bssid { d["bssid"] = b }
    if let sec = securityLabel(n) { d["security"] = sec }
    if let b = n.bssid, let cur = currentBSSID, b.lowercased() == cur.lowercased() { d["current"] = true }
    return d
}

func currentInfo(_ iface: CWInterface) -> [String: Any]? {
    let rssi = iface.rssiValue()
    guard rssi != 0 else { return nil }  // not associated
    var d: [String: Any] = ["rssi": rssi, "noise": iface.noiseMeasurement(), "tx_rate": iface.transmitRate()]
    d.merge(channelInfo(iface.wlanChannel())) { $1 }
    if let s = iface.ssid() { d["ssid"] = s }
    if let b = iface.bssid() { d["bssid"] = b }
    if let name = iface.interfaceName { d["interface"] = name }
    return d
}

func write(_ object: Any, to path: String, append: Bool = false) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: []) else { return }
    if append {
        var line = data
        line.append(0x0A)
        if let handle = FileHandle(forWritingAtPath: path) {
            handle.seekToEndOfFile()
            handle.write(line)
            handle.closeFile()
        } else {
            FileManager.default.createFile(atPath: path, contents: line)
        }
    } else {
        try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
    }
}

// --- main ----------------------------------------------------------------------------

NSApplication.shared.setActivationPolicy(.accessory)

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: subnetry-wifi-helper <status|auth|scan|current|monitor> <out> [...]\n".data(using: .utf8)!)
    exit(2)
}
let mode = args[1]
let out = args[2]
let auth = LocationAuth()
var result: [String: Any] = [
    "auth": auth.statusName,
    "services_enabled": CLLocationManager.locationServicesEnabled(),
]

guard let iface = CWWiFiClient.shared().interface() else {
    result["error"] = "No Wi-Fi interface found."
    write(result, to: out)
    exit(0)
}

switch mode {
case "status":
    break

case "auth":
    auth.request(timeout: 60)
    result["auth"] = auth.statusName

case "scan":
    let current = iface.bssid()
    do {
        let networks = try iface.scanForNetworks(withName: nil)
        result["networks"] = networks.map { networkInfo($0, currentBSSID: current) }
    } catch {
        result["error"] = "Wi-Fi scan failed: \(error.localizedDescription)"
    }
    if let cur = currentInfo(iface) { result["current"] = cur }

case "current":
    if let cur = currentInfo(iface) { result["current"] = cur }

case "monitor":
    let interval = args.count > 3 ? max(0.5, Double(args[3]) ?? 1.0) : 1.0
    let stopFile = args.count > 4 ? args[4] : out + ".stop"
    let parentPid: pid_t = args.count > 5 ? pid_t(args[5]) ?? 0 : 0
    let started = Date()
    while !FileManager.default.fileExists(atPath: stopFile) && Date().timeIntervalSince(started) < 7200 {
        if parentPid > 0 && kill(parentPid, 0) != 0 { break }  // Subnetry went away
        var line: [String: Any] = ["t": Date().timeIntervalSince(started), "auth": auth.statusName]
        if let cur = currentInfo(iface) { line["current"] = cur }
        write(line, to: out, append: true)
        RunLoop.current.run(until: Date().addingTimeInterval(interval))
    }
    exit(0)

default:
    result["error"] = "Unknown mode: \(mode)"
}

write(result, to: out)
exit(0)
