"use strict";
// Public IP: this connection's internet-facing address, ISP and location; plus lookups of other IPs.

function ipTiles(info) {
  const place = [info.city, info.region, info.country].filter(Boolean).map(esc).join(", ") || "–";
  const map = info.latitude != null
    ? `<a href="https://www.openstreetmap.org/?mlat=${info.latitude}&mlon=${info.longitude}#map=10/${info.latitude}/${info.longitude}" target="_blank" rel="noopener">View on map</a>`
    : "";
  return [
    tile(info.is_self ? "Public IPv4" : "IP address", esc(info.ip), info.is_self ? "Your router's internet-facing address" : "", "small mono"),
    info.is_self ? tile("Public IPv6", info.ipv6 ? esc(info.ipv6) : "None", info.ipv6 ? "Your connection supports IPv6" : "No IPv6 connectivity detected", "small mono") : "",
    tile("Provider (ISP)", esc(info.isp || "–"), info.asn ? `Network ${esc(info.asn)}` : "", "small"),
    tile("Approximate location", place, map, "small"),
    tile("Time zone", esc(info.timezone || "–"), "", "small"),
    tile("Reverse DNS", esc(info.hostname || "–"), "Name your ISP assigned to this address", "small mono"),
  ].join("");
}

async function loadPublicIp() {
  const status = $("#ip-status");
  setStatus(status, "Looking up your public IP…");
  try {
    const info = await api("/api/ipinfo");
    $("#ip-tiles").innerHTML = ipTiles(info);
    setStatus(status, `Looked up via ${esc(info.source)}.`);
  } catch (err) {
    $("#ip-tiles").innerHTML = "";
    setStatus(status, esc(err.message), true);
  }
}
let ipLoaded = false;
loaders.ip = () => { if (!ipLoaded) { ipLoaded = true; loadPublicIp(); } };
$("#ip-refresh").addEventListener("click", loadPublicIp);

$("#ip-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ip-query").value.trim();
  const status = $("#ip-lookup-status");
  if (!q) return;
  setStatus(status, `Looking up ${esc(q)}…`);
  $("#ip-lookup-tiles").innerHTML = "";
  try {
    const info = await api(`/api/ipinfo?ip=${encodeURIComponent(q)}`);
    $("#ip-lookup-tiles").innerHTML = ipTiles(info);
    setStatus(status, "");
  } catch (err) {
    setStatus(status, esc(err.message), true);
  }
});
