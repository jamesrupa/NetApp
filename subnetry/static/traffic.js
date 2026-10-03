"use strict";
// Traffic Analyzer: live tshark capture summarized as protocols, devices, destinations and insights.

const trSeries = { in: [], out: [] };
let trRun = null;
let trDuration = 60;
let trStatusLoaded = false;

async function loadTraffic() {
  if (trStatusLoaded) return;
  trStatusLoaded = true;
  drawTraffic();
  let st;
  try {
    st = await api("/api/traffic/status");
  } catch (err) {
    setStatus($("#tr-status"), esc(err.message), true);
    return;
  }
  if (!st.installed || st.error) {
    const box = $("#tr-missing");
    box.hidden = false;
    box.innerHTML = st.installed
      ? `<h2>Can't list capture interfaces</h2><p>${esc(st.error)}</p>`
      : `<h2>Wireshark (tshark) isn't installed</h2><p>${esc(st.install_help)}</p>`;
    $("#tr-start").disabled = true;
    if (!st.interfaces.length) return;
  }
  // Pre-select the interface carrying this machine's main network.
  const o = await loadOverview();
  const primary = o?.networks?.[0]?.interface?.toLowerCase();
  const skip = /^(any|lo|loopback|bluetooth|dbus|nflog|nfqueue|usbmon|ifb|dpauxmon|sdjournal|ciscodump|randpkt|sshdump|udpdump|wifidump|etwdump|androiddump)/i;
  const ifaces = st.interfaces.filter((i) => !skip.test(i.name) && !/loopback/i.test(i.description));
  const rest = st.interfaces.filter((i) => !ifaces.includes(i));
  const pick = ifaces.find((i) => primary && (i.name.toLowerCase() === primary || i.description.toLowerCase().includes(primary)))
    || ifaces.find((i) => /wi-?fi|wlan|wireless|ethernet|^en\d|^eth/i.test(`${i.name} ${i.description}`)) || ifaces[0];
  $("#tr-iface").innerHTML = [...ifaces, ...rest].map((i) =>
    `<option value="${esc(i.name)}" ${i === pick ? "selected" : ""}>${esc(i.description === i.name ? i.name : `${i.description}`)}</option>`).join("");
}
loaders.traffic = loadTraffic;

function drawTraffic() {
  lineChart($("#tr-chart"), [
    { name: "Download (in)", cls: "series-1", points: trSeries.in },
    { name: "Upload (out)", cls: "series-2", points: trSeries.out },
  ], { xMax: trDuration, xLabel: (x) => fmtT(x), yUnit: "Mbps" });
}

chartRedrawers.push(() => { if (!$("#tab-traffic").hidden) drawTraffic(); });

const hostName = (ip) => hosts.get(ip)?.hostname;  // from the Network Scanner, if it has been run

function renderTraffic(ev, live) {
  const devicesSeen = ev.devices.length;
  $("#tr-tiles").innerHTML = [
    tile("Packets", ev.packets.toLocaleString()),
    tile("Data captured", fmtBytes(ev.bytes)),
    tile("Local devices seen", devicesSeen),
    tile(live ? "Current rate" : "Average rate",
      live ? `${(ev.in_mbps + ev.out_mbps).toFixed(2)}<small>Mbps</small>` : `${((ev.bytes * 8) / 1e6 / Math.max(ev.seconds, 1)).toFixed(2)}<small>Mbps</small>`,
      live ? `↓ ${ev.in_mbps.toFixed(2)} · ↑ ${ev.out_mbps.toFixed(2)}` : ""),
  ].join("");

  const total = ev.protocols.reduce((a, p) => a + p.bytes, 0) || 1;
  $("#tr-protocols").innerHTML = ev.protocols.slice(0, 10).map((p) => {
    const pct = (100 * p.bytes) / total;
    return `<div class="row" title="${esc(p.description)}">
      <div class="name">${esc(p.label)}<span class="sub">${esc(p.description)}</span></div>
      <div class="track"><div class="fill" style="width:${Math.max(pct, 0.5)}%"></div></div>
      <div class="val">${pct < 1 ? "<1" : pct.toFixed(0)}% · ${fmtBytes(p.bytes)}</div></div>`;
  }).join("") || `<p class="sub">No packets yet.</p>`;

  $("#tr-insights").innerHTML = ev.insights.length ? ev.insights.map(recCard).join("") : `<p class="sub">Nothing unusual so far.</p>`;

  $("#tr-devices").innerHTML = ev.devices.map((d) => `<tr>
      <td class="mono">${esc(d.ip)}${hostName(d.ip) ? `<span class="sub">${esc(hostName(d.ip))}</span>` : ""}</td>
      <td class="num">${fmtBytes(d.sent)}</td><td class="num">${fmtBytes(d.received)}</td><td>${esc(d.top_protocol || "–")}</td></tr>`).join("")
    || `<tr><td colspan="4" class="empty">–</td></tr>`;

  $("#tr-dests").innerHTML = ev.destinations.map((d) => `<tr>
      <td>${d.name ? `<b>${esc(d.name)}</b><span class="sub mono">${esc(d.ips.join(", "))}</span>` : `<span class="mono">${esc(d.ips[0])}</span><span class="sub">No name seen</span>`}</td>
      <td>${esc(d.protocol)}</td><td class="num">${fmtBytes(d.bytes)}</td></tr>`).join("")
    || `<tr><td colspan="3" class="empty">–</td></tr>`;

  const tags = { dns: ["Lookup", "info"], tls: ["Secure", "good"], warning: ["Plaintext", "warning"] };
  $("#tr-feed").innerHTML = ev.feed.map((f) => {
    const [label, cls] = tags[f.kind] || ["Event", "info"];
    return `<li><span class="when">${fmtT(f.t)}</span><span class="tag ${cls}">${label}</span><span>${esc(f.text)}</span></li>`;
  }).join("") || `<li class="sub">No lookups or connections yet.</li>`;
}

$("#tr-form").addEventListener("submit", (e) => {
  e.preventDefault();
  trDuration = Number($("#tr-duration").value);
  trSeries.in = []; trSeries.out = [];
  drawTraffic();
  const status = $("#tr-status");
  const params = new URLSearchParams({
    interface: $("#tr-iface").value, duration: trDuration, filter: $("#tr-filter").value, save: $("#tr-save").checked,
  });
  $("#tr-start").hidden = true;
  $("#tr-stop").hidden = false;
  setStatus(status, "Starting capture…");
  let failed = false;
  trRun = stream(`/api/traffic/capture?${params}`, (ev) => {
    if (ev.type === "start") setStatus(status, `Capturing on ${esc(ev.interface)} for ${fmtT(ev.duration)}${ev.filter ? ` (filter: ${esc(ev.filter)})` : ""}…`);
    if (ev.type === "stats") {
      trSeries.in.push({ x: ev.t, y: ev.in_mbps });
      trSeries.out.push({ x: ev.t, y: ev.out_mbps });
      drawTraffic();
      renderTraffic(ev, true);
      setStatus(status, `Capturing… ${fmtT(ev.t)} of ${fmtT(trDuration)}`);
    }
    if (ev.type === "done") {
      renderTraffic(ev, false);
      const saved = ev.pcap
        ? ` Saved to <span class="mono">${esc(ev.pcap_path)}</span>. <a href="/api/traffic/captures/${encodeURIComponent(ev.pcap)}" download>Download .pcapng</a> to open it in Wireshark.`
        : "";
      setStatus(status, `Capture finished: ${ev.packets.toLocaleString()} packets in ${ev.seconds}s.${saved}`);
    }
    if (ev.type === "error") { failed = true; setStatus(status, esc(ev.message), true); }
  }, (err) => {
    $("#tr-start").hidden = false;
    $("#tr-stop").hidden = true;
    trRun = null;
    if (err && !failed) setStatus(status, esc(err.message), true);
  });
});

$("#tr-stop").addEventListener("click", () => {
  trRun?.stop();
  setStatus($("#tr-status"), "Capture stopped. The results above cover what was captured so far.");
});
