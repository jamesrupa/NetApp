"use strict";
// Dashboard: live connection quality plus the latest results from every tool, at a glance.

const DASH_WINDOW = 120;  // seconds of history on the live latency chart
const dash = { run: null, gw: [], inet: [], samples: 0, inetFails: 0, retry: null, publicIp: null };
const dashDial = createDial($("#dash-dial"), { ticks: [-90, -80, -70, -60, -50, -40, -30] });
dashDial.set({ text: "–", unit: "dBm", label: "Checking…" });

function dashTile(id, label, unit = "") {
  return `<div class="tile" id="dt-${id}"><div class="tile-label">${label}</div>
    <div class="tile-value"><span class="dv">–</span>${unit ? `<small>${unit}</small>` : ""}</div>
    <div class="tile-note">&nbsp;</div></div>`;
}
$("#dash-tiles").innerHTML = [
  dashTile("conn", "Connection"), dashTile("wifi", "Wi-Fi signal", "dBm"), dashTile("down", "<i class=\"key key-1\"></i>Download", "Mbps"),
  dashTile("up", "<i class=\"key key-2\"></i>Upload", "Mbps"), dashTile("score", "Health score", "/ 100"), dashTile("devices", "Devices"),
].join("");
const dt = (id, part) => $(`#dt-${id} ${part === "note" ? ".tile-note" : ".dv"}`);

function drawLatency() {
  const last = Math.max(0, ...dash.gw.map((p) => p.x), ...dash.inet.map((p) => p.x));
  const from = Math.max(0, last - DASH_WINDOW);
  const view = (pts) => pts.filter((p) => p.x >= from).map((p) => ({ x: p.x - from, y: p.y }));
  lineChart($("#dash-latency"), [
    { name: "Router", cls: "series-1", points: view(dash.gw) },
    { name: "Internet", cls: "series-2", points: view(dash.inet) },
  ], { xMax: DASH_WINDOW, xLabel: (x) => (x >= DASH_WINDOW - 1 ? "now" : `-${Math.round(DASH_WINDOW - x)}s`), yUnit: "ms" });
}
chartRedrawers.push(() => { if (!$("#tab-dashboard").hidden) drawLatency(); });

function latencyStats(points) {
  const ys = points.slice(-60).map((p) => p.y);
  if (!ys.length) return null;
  const avg = ys.reduce((a, b) => a + b, 0) / ys.length;
  const jitter = ys.length > 1 ? ys.slice(1).reduce((a, y, i) => a + Math.abs(y - ys[i]), 0) / (ys.length - 1) : 0;
  return { avg, jitter };
}

function dashRenderWifi(w) {
  if (!w) {
    dt("wifi").textContent = "–";
    dt("wifi", "note").textContent = "Not on Wi-Fi (wired, or Wi-Fi unavailable)";
    dashDial.set({ text: "–", unit: "dBm", label: "No Wi-Fi" });
    $("#dash-wifi-name").textContent = "";
    return;
  }
  const q = quality(w.signal_dbm);
  animateNumber(dt("wifi"), w.signal_dbm);
  const name = w.redacted ? "Name hidden by macOS" : (w.ssid || "(hidden)");
  dt("wifi", "note").innerHTML = `<span class="t-${q.cls}">${q.label}</span> · ${esc(name)}`;
  $("#dash-wifi-name").textContent = `${name} · ${w.band || ""} ch ${w.channel ?? "?"}`;
  dashDial.set({ value: w.signal_dbm, text: String(w.signal_dbm), unit: "dBm", label: q.label, tone: q.cls });
}

function onLiveSample(ev) {
  dash.samples += 1;
  if (ev.gateway_ms != null) dash.gw.push({ x: ev.t, y: ev.gateway_ms });
  if (ev.internet_ms != null) dash.inet.push({ x: ev.t, y: ev.internet_ms });
  else dash.inetFails += 1;
  // keep a little more than the visible window
  dash.gw = dash.gw.filter((p) => p.x >= ev.t - DASH_WINDOW - 5);
  dash.inet = dash.inet.filter((p) => p.x >= ev.t - DASH_WINDOW - 5);

  const conn = dt("conn");
  conn.textContent = ev.online ? "ONLINE" : "OFFLINE";
  conn.className = `dv ${ev.online ? "t-good" : "t-critical"}`;
  $("#dt-conn").classList.toggle("is-online", ev.online);
  $("#dt-conn").classList.toggle("is-offline", !ev.online);
  dt("conn", "note").textContent = [
    ev.internet_ms != null && `Internet ${Math.round(ev.internet_ms)} ms`,
    ev.gateway_ms != null && `Router ${Math.round(ev.gateway_ms)} ms`,
    ev.dns_ms != null && `DNS ${Math.round(ev.dns_ms)} ms`,
  ].filter(Boolean).join(" · ") || (ev.gateway ? "No replies yet" : "No router found");

  const s = latencyStats(dash.inet);
  const loss = Math.round((100 * dash.inetFails) / dash.samples);
  $("#dash-latency-note").textContent = s
    ? `Internet: average ${s.avg.toFixed(0)} ms · jitter ${s.jitter.toFixed(1)} ms · ${loss}% of checks failed`
    : "Waiting for replies from the internet…";
  drawLatency();
  if (ev.wifi_checked) dashRenderWifi(ev.wifi);
}

function startLive() {
  if (dash.run) return;
  clearTimeout(dash.retry);
  $("#dash-live").classList.add("on");
  dash.run = stream("/api/dashboard/live", (ev) => { if (ev.type === "sample") onLiveSample(ev); }, () => {
    dash.run = null;
    $("#dash-live").classList.remove("on");
    if (currentTab === "dashboard") dash.retry = setTimeout(startLive, 5000);  // reconnect after a hiccup
  });
}
function stopLive() {
  clearTimeout(dash.retry);
  dash.run?.stop();
  dash.run = null;
  $("#dash-live").classList.remove("on");
}

function renderLast(last) {
  const sp = last.speed;
  if (sp) {
    animateNumber(dt("down"), sp.download_mbps, { decimals: 1 });
    animateNumber(dt("up"), sp.upload_mbps, { decimals: 1 });
    dt("down", "note").textContent = `${sp.engine || "Speed test"} · ${timeAgo(sp.at)}`;
    dt("up", "note").textContent = `Ping ${Math.round(sp.latency_ms ?? 0)} ms · ${timeAgo(sp.at)}`;
  } else {
    dt("down", "note").textContent = dt("up", "note").textContent = "No speed test yet";
  }
  const h = last.health;
  if (h?.score) {
    animateNumber(dt("score"), h.score.value);
    const tone = h.score.value >= 75 ? "good" : h.score.value >= 50 ? "warning" : "critical";
    dt("score").className = `dv t-${tone}`;
    dt("score", "note").textContent = `${h.score.grade} · ${h.mode} scan · ${timeAgo(h.at)}`;
  } else {
    dt("score", "note").textContent = "No health check yet";
  }
  const d = last.devices;
  if (d) {
    animateNumber(dt("devices"), d.count);
    dt("devices", "note").textContent = `${d.network || ""} · ${timeAgo(d.at)}`;
  } else {
    dt("devices", "note").textContent = "No device scan yet";
  }

  const recs = $("#dash-recs");
  if (h?.top?.length) {
    recs.innerHTML = h.top.map((r) => recCard({ ...r, detail: "" })).join("");
  } else if (h) {
    recs.innerHTML = `<p class="sub">Your last check (${timeAgo(h.at)}) found nothing that needs changing. ✔</p>`;
  } else {
    recs.innerHTML = `<p class="sub">Run a <b>Full scan</b> to get personalised recommendations for your network.</p>`;
  }
}

function renderFacts(s) {
  const rows = [
    ["Host", s.hostname], ["OS", s.os], ["Local IP", s.local_ip], ["Network", s.network && `${s.network} (${s.interface})`],
    ["Router", s.gateway], ["DNS", s.dns_servers?.join(", ")], ["Public IP", dash.publicIp || "…"],
  ];
  $("#dash-facts").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd class="mono">${esc(v || "–")}</dd>`).join("");
}

async function loadDashboard() {
  startLive();
  try {
    const s = await api("/api/dashboard");
    renderLast(s.last || {});
    renderFacts(s);
    if (!dash.publicIp) {
      api("/api/ipinfo").then((ip) => {
        dash.publicIp = `${ip.ip}${ip.isp ? ` · ${ip.isp}` : ""}`;
        renderFacts(s);
      }).catch(() => { dash.publicIp = "unavailable"; renderFacts(s); });
    }
  } catch (err) {
    $("#dash-recs").innerHTML = `<p class="status error">${esc(err.message)}</p>`;
  }
}
loaders.dashboard = loadDashboard;
leavers.dashboard = stopLive;
drawLatency();

// Quick actions jump to a tool and start it.
$("#tab-dashboard").addEventListener("click", (e) => {
  const goto = e.target.closest("[data-goto]")?.dataset.goto;
  if (goto) return showTab(goto);
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  if (action === "quick" || action === "full") {
    showTab("health");
    startDiagnosis(action);
  } else if (action === "speed") {
    showTab("speed");
    setTimeout(() => $("#speed-form").requestSubmit(), 400);  // let the engine list load first
  } else if (action === "scan") {
    showTab("scan");
    loadOverview().then(() => $("#scan-form").requestSubmit());
  } else if (action === "wifi") {
    showTab("wifi");
    $("#wifi-start").click();
  } else if (action === "monitor") {
    showTab("monitor");
  }
});
