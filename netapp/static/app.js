"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SVG_NS = "http://www.w3.org/2000/svg";

async function api(path) {
  const r = await fetch(path);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status} ${r.statusText}`);
  return body;
}

/** Open a Server-Sent Events stream; calls onEvent(obj) per message, onEnd() when closed. */
function stream(path, onEvent, onEnd) {
  const es = new EventSource(path);
  let finished = false;
  const finish = (err) => { if (!finished) { finished = true; es.close(); onEnd(err); } };
  es.onmessage = (m) => onEvent(JSON.parse(m.data));
  es.addEventListener("end", () => finish());
  es.onerror = () => finish(new Error("Connection to the NetApp server was lost."));
  return { stop: () => finish() };  // stopping closes the stream; the server then stops the tool
}

function fmtBytes(n) {
  if (n == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

const SEV = {
  critical: { icon: "✖", label: "Critical" },
  warning: { icon: "▲", label: "Warning" },
  info: { icon: "ℹ", label: "Info" },
  good: { icon: "✔", label: "Looks good" },
};

function recCard(r) {
  return `<article class="rec ${r.severity}">
      <div class="sev">${SEV[r.severity].icon} ${SEV[r.severity].label}${r.category ? ` <span class="cat">· ${esc(r.category)}</span>` : ""}</div>
      <h3>${esc(r.title)}</h3>
      <p>${esc(r.detail)}</p>
      ${r.action ? `<p class="action"><b>Recommended change:</b> ${esc(r.action)}</p>` : ""}
    </article>`;
}

function tile(label, value, note = "", cls = "") {
  return `<div class="tile"><div class="tile-label">${label}</div><div class="tile-value ${cls}">${value}</div>${note ? `<div class="tile-note">${note}</div>` : ""}</div>`;
}

function setStatus(el, text, isError = false) {
  el.innerHTML = text;
  el.classList.toggle("error", isError);
}

// --- tabs & theme ---------------------------------------------------------------------

const loaders = {};
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `tab-${name}`; });
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  loaders[name]?.();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { return null; }
}
const savedTheme = storage("netapp-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
$("#theme-toggle").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  storage("netapp-theme", next);
});

// --- tooltip --------------------------------------------------------------------------

const tip = $("#tooltip");
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let left = x + 14, top = y - r.height - 10;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top < 8) top = y + 16;
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
const hideTip = () => { tip.hidden = true; };

// --- charts ---------------------------------------------------------------------------

function svgEl(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * mag) return m * mag;
  return 10 * mag;
}

/**
 * Line chart. series = [{name, cls, unit, points:[{x,y}]}]; xMax fixes the x domain.
 * Hover shows a crosshair and the value of every series at that x.
 */
function lineChart(container, series, { xMax, xLabel = (x) => x, yUnit = "" }) {
  const W = 760, H = 260, m = { t: 12, r: 16, b: 28, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const yMax = niceMax(Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.y))));
  const sx = (x) => m.l + (x / xMax) * iw;
  const sy = (y) => m.t + ih - (y / yMax) * ih;

  container.innerHTML = "";
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map((s) => s.name).join(" and ") + " over time" }, container);
  for (let i = 0; i <= 4; i++) {
    const v = (yMax / 4) * i;
    svgEl("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: sy(v), y2: sy(v) }, svg);
    svgEl("text", { class: "axis-label", x: m.l - 8, y: sy(v) + 4, "text-anchor": "end" }, svg).textContent = +v.toFixed(2);
  }
  const ticks = Math.min(8, Math.floor(xMax));
  for (let i = 0; i <= ticks; i++) {
    const v = (xMax / ticks) * i;
    svgEl("text", { class: "axis-label", x: sx(v), y: H - 8, "text-anchor": "middle" }, svg).textContent = xLabel(v);
  }
  for (const s of series) {
    if (s.points.length < 2) continue;
    svgEl("path", { class: s.cls, d: s.points.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join("") }, svg);
  }

  // hover layer
  const cross = svgEl("line", { class: "crosshair", y1: m.t, y2: m.t + ih, visibility: "hidden" }, svg);
  const dots = series.map((s, i) => svgEl("circle", { class: `dot-${i + 1}`, r: 4, visibility: "hidden" }, svg));
  const hit = svgEl("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih }, svg);
  hit.addEventListener("mousemove", (e) => {
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const x = ((pt.matrixTransform(svg.getScreenCTM().inverse()).x - m.l) / iw) * xMax;
    const rows = [];
    series.forEach((s, i) => {
      if (!s.points.length) { dots[i].setAttribute("visibility", "hidden"); return; }
      const p = s.points.reduce((a, b) => (Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a));
      dots[i].setAttribute("cx", sx(p.x)); dots[i].setAttribute("cy", sy(p.y));
      dots[i].setAttribute("visibility", "visible");
      rows.push(`<i class="key key-${i + 1}"></i> ${esc(s.name)} <b>${p.y.toFixed(1)}</b> ${yUnit}`);
    });
    if (!rows.length) return;
    cross.setAttribute("x1", sx(x)); cross.setAttribute("x2", sx(x));
    cross.setAttribute("visibility", "visible");
    showTip(`${xLabel(x)}<br>${rows.join("<br>")}`, e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTip();
  });
}

/** Bar chart of counts per category. bars = [{label, value, tip}]. */
function barChart(container, bars, { ariaLabel }) {
  const W = 520, H = 200, m = { t: 12, r: 8, b: 26, l: 32 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const yMax = Math.max(2, ...bars.map((b) => b.value));
  const step = iw / bars.length, bw = Math.max(4, Math.min(28, step - 2));
  const sy = (y) => m.t + ih - (y / yMax) * ih;

  container.innerHTML = "";
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": ariaLabel }, container);
  const yTicks = yMax <= 5 ? yMax : 4;
  for (let i = 0; i <= yTicks; i++) {
    const v = Math.round((yMax / yTicks) * i);
    svgEl("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: sy(v), y2: sy(v) }, svg);
    svgEl("text", { class: "axis-label", x: m.l - 6, y: sy(v) + 4, "text-anchor": "end" }, svg).textContent = v;
  }
  const labelEvery = Math.ceil(bars.length / 14);
  bars.forEach((b, i) => {
    const cx = m.l + step * i + step / 2;
    if (b.value > 0) {
      const h = (b.value / yMax) * ih, x = cx - bw / 2, y = m.t + ih - h, r = Math.min(4, bw / 2, h);
      // rounded data-end, square at the baseline
      svgEl("path", { class: "bar", d: `M${x},${m.t + ih}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${m.t + ih}Z` }, svg);
    }
    if (i % labelEvery === 0) svgEl("text", { class: "axis-label", x: cx, y: H - 8, "text-anchor": "middle" }, svg).textContent = b.label;
    const hit = svgEl("rect", { class: "hit", x: cx - step / 2, y: m.t, width: step, height: ih }, svg);
    hit.addEventListener("mousemove", (e) => showTip(b.tip, e.clientX, e.clientY));
    hit.addEventListener("mouseleave", hideTip);
  });
}

// --- Overview -------------------------------------------------------------------------

let overviewCache = null;
async function loadOverview(force = false) {
  if (overviewCache && !force) return overviewCache;
  const tiles = $("#overview-tiles");
  try {
    const o = await api("/api/overview");
    overviewCache = o;
    const primary = o.networks[0];
    tiles.innerHTML = [
      ["Hostname", esc(o.hostname), esc(o.os)],
      ["Local IP", primary ? esc(primary.address) : "–", primary ? `${esc(primary.network)} on ${esc(primary.interface)}` : "No active network"],
      ["Default gateway", esc(o.gateway || "–"), "Usually your router"],
      ["DNS servers", o.dns_servers.length ? o.dns_servers.map(esc).join("<br>") : "–", ""],
      ["Public IP", `<span id="public-ip">…</span>`, "As seen by the internet"],
    ].map(([label, value, note]) => `
      <div class="tile"><div class="tile-label">${label}</div>
      <div class="tile-value small mono">${value}</div><div class="tile-note">${note}</div></div>`).join("");
    $("#iface-rows").innerHTML = o.interfaces.map((i) => `
      <tr>
        <td><b>${esc(i.name)}</b></td>
        <td><span class="badge ${i.is_up ? "accent" : ""}">${i.is_up ? "Up" : "Down"}</span></td>
        <td class="mono">${i.ipv4.map((a) => esc(a.address)).join("<br>") || "–"}</td>
        <td class="mono">${i.ipv4.map((a) => esc(a.network || "")).join("<br>") || "–"}</td>
        <td class="mono">${esc(i.mac || "–")}</td>
        <td class="num">${i.speed_mbps ? `${i.speed_mbps} Mbps` : "–"}</td>
      </tr>`).join("") || `<tr><td colspan="6" class="empty">No interfaces found.</td></tr>`;
    api("/api/public-ip")
      .then((r) => { $("#public-ip").textContent = r.ip; })
      .catch(() => { $("#public-ip").textContent = "unavailable"; });
    fillScanNetworks(o);
  } catch (err) {
    tiles.innerHTML = `<div class="tile"><div class="tile-label">Error</div><div class="tile-note">${esc(err.message)}</div></div>`;
  }
  return overviewCache;
}
loaders.overview = () => loadOverview();
$("#overview-refresh").addEventListener("click", () => loadOverview(true));

// --- Speed test -----------------------------------------------------------------------

const SPEED_DURATION = 8;
const speed = { down: [], up: [] };
function drawSpeed() {
  // Cloudflare phases last 8 s; Speedtest.net decides its own duration, so the axis grows to fit.
  const lastT = Math.max(0, ...speed.down.map((p) => p.x), ...speed.up.map((p) => p.x));
  lineChart($("#speed-chart"), [
    { name: "Download", cls: "series-1", points: speed.down },
    { name: "Upload", cls: "series-2", points: speed.up },
  ], { xMax: Math.max(SPEED_DURATION, Math.ceil(lastT)), xLabel: (x) => `${x.toFixed(1).replace(/\.0$/, "")}s`, yUnit: "Mbps" });
}
drawSpeed();

function resetSpeed() {
  speed.down = []; speed.up = [];
  ["#sp-ping", "#sp-jitter", "#sp-down", "#sp-up", "#sp-loss"].forEach((s) => { $(s).textContent = "–"; });
  $("#sp-loss-tile").hidden = true;
  $("#sp-server-info").innerHTML = "";
  drawSpeed();
}

const SPEED_LABELS = { latency: "Measuring latency…", download: "Testing download…", upload: "Testing upload…" };
const safeUrl = (u) => (typeof u === "string" && u.startsWith("https://") ? u : null);

function speedServerLine(ev) {
  const s = ev.server || {};
  const parts = [ev.engine || ev.label, s.name && `Server: ${s.name}${s.location ? ` (${s.location})` : ""}`, ev.isp && `ISP: ${ev.isp}`]
    .filter(Boolean).map(esc);
  const url = safeUrl(ev.result_url);
  return parts.join(" · ") + (url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener">View result on speedtest.net</a>` : "");
}

/** Apply one speed-test event to the Speed Test tab. Returns a short status line (or null). */
function applySpeedEvent(ev) {
  if (ev.type === "error") return null;
  if (ev.phase === "info") {
    if (ev.type === "engine") $("#sp-server-info").textContent = ev.label;
    if (ev.type === "server") $("#sp-server-info").innerHTML = speedServerLine({ ...ev, engine: "Speedtest.net (Ookla)" });
    return null;
  }
  if (ev.phase === "latency") {
    if (ev.type === "sample") $("#sp-ping").textContent = ev.ms.toFixed(0);
    if (ev.type === "result") {
      $("#sp-ping").textContent = ev.latency_ms.toFixed(0);
      $("#sp-jitter").textContent = ev.jitter_ms.toFixed(1);
    }
  }
  if (ev.phase === "download" || ev.phase === "upload") {
    const el = $(ev.phase === "download" ? "#sp-down" : "#sp-up");
    if (ev.type === "sample") {
      speed[ev.phase === "download" ? "down" : "up"].push({ x: ev.t, y: ev.mbps });
      el.textContent = ev.mbps.toFixed(1);
      drawSpeed();
      return `${SPEED_LABELS[ev.phase]} ${ev.mbps.toFixed(1)} Mbps`;
    }
    if (ev.type === "result") el.textContent = ev.mbps.toFixed(1);
  }
  if (ev.type === "start") return SPEED_LABELS[ev.phase] || null;
  if (ev.phase === "done") {
    if (ev.packet_loss != null) {
      $("#sp-loss").textContent = ev.packet_loss.toFixed(1);
      $("#sp-loss-tile").hidden = false;
    }
    $("#sp-server-info").innerHTML = speedServerLine(ev);
    return `Download ${ev.download_mbps} Mbps · Upload ${ev.upload_mbps} Mbps · Ping ${ev.latency_ms} ms`;
  }
  return null;
}

let speedStatus = null;
async function loadSpeed() {
  if (speedStatus) return;
  try {
    speedStatus = (await api("/api/speedtest/status")).ookla;
  } catch {
    return;
  }
  const engine = $("#sp-engine");
  if (speedStatus.installed) {
    $("#sp-terms").innerHTML = `Speedtest.net tests use the official Speedtest® CLI by Ookla (${esc(speedStatus.version)}). Running one accepts Ookla's
      <a href="${esc(speedStatus.terms_url)}" target="_blank" rel="noopener">EULA</a> and
      <a href="${esc(speedStatus.privacy_url)}" target="_blank" rel="noopener">Privacy Policy</a>; results are shared with Speedtest.net.`;
    api("/api/speedtest/servers").then(({ servers }) => {
      $("#sp-server").insertAdjacentHTML("beforeend", servers.map((s) =>
        `<option value="${esc(s.id)}">${esc(s.name)} – ${esc(s.location)}${s.country ? `, ${esc(s.country)}` : ""}</option>`).join(""));
    }).catch(() => {});
  } else {
    engine.value = "cloudflare";
    engine.querySelector('[value="ookla"]').textContent = "Speedtest.net (Ookla), not installed";
    const box = $("#sp-missing");
    box.hidden = false;
    box.innerHTML = `<h2>Install the Speedtest.net CLI</h2><p>${esc(speedStatus.conflict || speedStatus.install_help)}</p>
      <p class="sub">Until then, tests use Cloudflare's speed-test servers, which need no install.</p>`;
  }
  const sync = () => { $("#sp-server-wrap").hidden = engine.value !== "ookla"; };
  engine.addEventListener("change", sync);
  sync();
}
loaders.speed = loadSpeed;

$("#speed-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const btn = $("#speed-start"), status = $("#speed-status");
  btn.disabled = true;
  resetSpeed();
  const params = new URLSearchParams({ engine: $("#sp-engine").value, duration: SPEED_DURATION });
  if ($("#sp-engine").value === "ookla" && $("#sp-server").value) params.set("server_id", $("#sp-server").value);
  setStatus(status, $("#sp-engine").value === "ookla" ? "Finding the best Speedtest.net server…" : "Starting…");
  let failed = false;
  stream(`/api/speedtest?${params}`, (ev) => {
    if (ev.type === "error") { failed = true; setStatus(status, `Speed test failed: ${esc(ev.message)}`, true); return; }
    const line = applySpeedEvent(ev);
    if (line) setStatus(status, ev.phase === "done" ? `Done. ${line}.` : line);
  }, (err) => {
    btn.disabled = false;
    if (err && !failed) setStatus(status, esc(err.message), true);
  });
});

// --- Network scanner ------------------------------------------------------------------

function fillScanNetworks(o) {
  const sel = $("#scan-net");
  if (sel.options.length) return;
  sel.innerHTML = o.networks.map((n) => `<option value="${esc(n.network)}">${esc(n.network)} (${esc(n.interface)})</option>`).join("")
    || `<option value="">No active network</option>`;
}
loaders.scan = () => loadOverview();

const hosts = new Map();
const ipKey = (ip) => ip.split(".").reduce((a, o) => a * 256 + Number(o), 0);

function renderHosts() {
  const rows = [...hosts.values()].sort((a, b) => ipKey(a.ip) - ipKey(b.ip));
  $("#scan-rows").innerHTML = rows.map((h) => {
    const tags = [h.is_gateway && "Gateway", h.is_self && "This device"].filter(Boolean)
      .map((t) => `<span class="badge accent">${t}</span>`).join("");
    const mac = h.mac
      ? `${esc(h.mac)}${h.mac_randomized ? `<span class="sub">Private / randomized MAC</span>` : ""}`
      : "–";
    const ports = h.ports
      ? (h.ports.open.length ? h.ports.open.map((p) => `<span class="badge" title="${esc(p.service)}">${p.port} ${esc(p.service)}</span>`).join("") : `<span class="sub">No common ports open</span>`)
      : `${(h.open_ports || []).map((p) => `<span class="badge">${p}</span>`).join("")}<button class="btn small" data-ports="${esc(h.ip)}">${h.scanning ? "Scanning…" : "Scan ports"}</button>`;
    return `<tr>
      <td class="mono"><b>${esc(h.ip)}</b> ${tags}</td>
      <td>${esc(h.hostname || "–")}</td>
      <td class="mono">${mac}</td>
      <td>${h.methods.map((m) => `<span class="badge">${m.toUpperCase()}</span>`).join("")}</td>
      <td class="num">${h.rtt_ms != null ? `${h.rtt_ms.toFixed(1)} ms` : "–"}</td>
      <td>${ports}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="empty">No devices found yet.</td></tr>`;
}

$("#scan-rows").addEventListener("click", async (e) => {
  const ip = e.target.dataset?.ports;
  if (!ip) return;
  const h = hosts.get(ip);
  h.scanning = true;
  renderHosts();
  try {
    h.ports = await api(`/api/ports?host=${encodeURIComponent(ip)}`);
  } catch (err) {
    setStatus($("#scan-status"), esc(err.message), true);
  }
  h.scanning = false;
  renderHosts();
});

$("#scan-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const btn = $("#scan-start"), status = $("#scan-status"), bar = $("#scan-bar");
  const cidr = $("#scan-cidr").value.trim() || $("#scan-net").value;
  btn.disabled = true;
  hosts.clear();
  renderHosts();
  bar.style.width = "0";
  let failed = false;
  const params = new URLSearchParams({ timeout: $("#scan-timeout").value });
  if (cidr) params.set("cidr", cidr);
  stream(`/api/scan?${params}`, (ev) => {
    if (ev.type === "start") setStatus(status, `Scanning ${esc(ev.network)} (${ev.total} addresses)…`);
    if (ev.type === "progress") bar.style.width = `${(100 * ev.done) / ev.total}%`;
    if (ev.type === "host") { hosts.set(ev.host.ip, { ...hosts.get(ev.host.ip), ...ev.host }); renderHosts(); }
    if (ev.type === "done") setStatus(status, `Found ${ev.hosts_found} device${ev.hosts_found === 1 ? "" : "s"} on ${esc(ev.network)} in ${ev.seconds}s.`);
    if (ev.type === "error") { failed = true; setStatus(status, esc(ev.message), true); }
  }, (err) => {
    btn.disabled = false;
    bar.style.width = failed || err ? "0" : "100%";
    if (err && !failed) setStatus(status, esc(err.message), true);
  });
});

// --- Wi-Fi scanner --------------------------------------------------------------------

function quality(dbm) {
  if (dbm == null) return { cls: "warning", label: "Unknown" };
  if (dbm >= -50) return { cls: "good", label: "Excellent" };
  if (dbm >= -60) return { cls: "good", label: "Good" };
  if (dbm >= -70) return { cls: "warning", label: "Fair" };
  return { cls: "critical", label: "Weak" };
}

function renderWifi(data) {
  const nets = data.networks;
  $("#wifi-rows").innerHTML = nets.map((n) => {
    const q = quality(n.signal_dbm);
    return `<tr>
      <td><b>${n.hidden ? `<span class="sub">(hidden network)</span>` : esc(n.ssid)}</b> ${n.in_use ? `<span class="badge accent">Connected</span>` : ""}</td>
      <td class="mono">${esc(n.bssid || "–")}</td>
      <td><div class="signal">
        <div class="signal-bar"><div class="q-${q.cls}" style="width:${n.signal_percent ?? 0}%"></div></div>
        <span class="t-${q.cls}">${q.label}</span>
        <span class="sub" style="display:inline">${n.signal_dbm ?? "?"} dBm</span>
      </div></td>
      <td class="num">${n.channel ?? "–"}</td>
      <td>${esc(n.band || "–")}</td>
      <td>${esc(n.security)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="empty">No networks found.</td></tr>`;

  const recs = data.channels.recommendations;
  const connected = nets.find((n) => n.in_use);
  $("#wifi-recs").innerHTML = [
    ["Networks found", nets.length, `${new Set(nets.map((n) => n.ssid).filter(Boolean)).size} unique SSIDs`],
    connected && ["Connected to", esc(connected.ssid), `Channel ${connected.channel ?? "?"} · ${connected.signal_dbm ?? "?"} dBm (${quality(connected.signal_dbm).label})`],
    recs["2.4 GHz"] && ["Best 2.4 GHz channel", recs["2.4 GHz"].channel, "Least overlap among 1 / 6 / 11"],
    recs["5 GHz"] && ["Best 5 GHz channel", recs["5 GHz"].channel, `${recs["5 GHz"].networks_on_channel} nearby network(s) on it`],
  ].filter(Boolean).map(([label, value, note]) => `
    <div class="tile"><div class="tile-label">${label}</div><div class="tile-value">${value}</div><div class="tile-note">${note}</div></div>`).join("");

  const charts = $("#wifi-charts");
  charts.innerHTML = "";
  for (const [band, usage] of Object.entries(data.channels.usage).sort()) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<div class="card-head"><h2>${esc(band)} channel usage</h2><span class="sub">Networks per channel</span></div><div class="chart"></div>`;
    charts.appendChild(card);
    const channels = band === "2.4 GHz"
      ? Array.from({ length: 13 }, (_, i) => i + 1)
      : Object.keys(usage).map(Number).sort((a, b) => a - b);
    const bars = channels.map((ch) => {
      const names = nets.filter((n) => n.band === band && n.channel === ch).map((n) => esc(n.ssid || "(hidden)"));
      const count = usage[ch] || 0;
      return {
        label: ch, value: count,
        tip: `Channel <b>${ch}</b>: <b>${count}</b> network${count === 1 ? "" : "s"}${names.length ? "<br>" + names.slice(0, 6).join("<br>") + (names.length > 6 ? `<br>+${names.length - 6} more` : "") : ""}`,
      };
    });
    barChart(card.querySelector(".chart"), bars, { ariaLabel: `${band} networks per channel` });
  }
}

$("#wifi-start").addEventListener("click", async () => {
  const btn = $("#wifi-start"), status = $("#wifi-status");
  btn.disabled = true;
  setStatus(status, "Scanning for Wi-Fi networks… (this can take several seconds)");
  try {
    const data = await api("/api/wifi");
    renderWifi(data);
    setStatus(status, `Found ${data.networks.length} access point${data.networks.length === 1 ? "" : "s"}.`);
  } catch (err) {
    setStatus(status, esc(err.message), true);
  }
  btn.disabled = false;
});

// --- Health check -----------------------------------------------------------------------

const STEP_ICON = { pending: "○", running: "●", done: "✔", error: "✖" };
const DETAIL_TAB = { speed: ["speed", "Speed Test"], wifi: ["wifi", "Wi-Fi Scanner"], devices: ["scan", "Network Scanner"] };
let hcReport = null;
let hcFilter = "all";

function renderSteps(steps) {
  $("#hc-steps").innerHTML = steps.map((st) => `
    <li class="${st.status}" data-step="${st.id}">
      <span class="icon" aria-hidden="true">${STEP_ICON[st.status]}</span>
      <span class="label">${esc(st.label)} <span class="sr-only">(${st.status})</span></span>
      <span class="detail">${st.detail ? esc(st.detail) : ""}</span>
    </li>`).join("");
}

function scoreTile(sc) {
  const r = 30, c = 2 * Math.PI * r;
  const tone = sc.value >= 75 ? "good" : sc.value >= 50 ? "warning" : "critical";
  return `<div class="tile score-ring">
    <svg width="76" height="76" viewBox="0 0 76 76" aria-hidden="true">
      <circle class="track" cx="38" cy="38" r="${r}" fill="none" stroke-width="8"/>
      <circle class="value" cx="38" cy="38" r="${r}" fill="none" stroke-width="8"
        style="stroke: var(--${tone})" stroke-dasharray="${(c * sc.value) / 100} ${c}"/>
    </svg>
    <div><div class="tile-label">Health score</div>
      <div class="tile-value">${sc.value}<small>/ 100</small></div>
      <div class="tile-note t-${tone}">${esc(sc.grade)}</div></div>
  </div>`;
}

function renderRecs() {
  const recs = hcReport.recommendations.filter((r) => hcFilter === "all" || r.severity === hcFilter);
  $("#hc-recs").innerHTML = recs.map(recCard).join("") || `<p class="muted">Nothing in this category.</p>`;
  document.querySelectorAll("#hc-filters button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === hcFilter)));
}

function renderReport(rep) {
  hcReport = rep;
  hcFilter = "all";
  const sc = rep.score, n = sc.counts;
  $("#hc-score").innerHTML = scoreTile(sc) + [
    ["critical", "Critical issues"], ["warning", "Warnings"], ["good", "Looks good"],
  ].map(([k, label]) => `<div class="tile"><div class="tile-label">${SEV[k].icon} ${label}</div><div class="tile-value">${n[k]}</div></div>`).join("");

  $("#hc-filters").innerHTML = [["all", `All (${rep.recommendations.length})`],
    ...Object.keys(SEV).filter((k) => n[k]).map((k) => [k, `${SEV[k].label} (${n[k]})`])]
    .map(([k, label]) => `<button type="button" data-filter="${k}">${label}</button>`).join("");
  renderRecs();

  const exports = rep.mode === "full"
    ? [["html", "HTML report"], ["json", "JSON"], ["recommendations.csv", "Recommendations CSV"], ["devices.csv", "Devices CSV"], ["wifi.csv", "Wi-Fi CSV"]]
    : [["html", "HTML report"], ["json", "JSON"], ["recommendations.csv", "Recommendations CSV"]];
  $("#hc-export").innerHTML = exports.map(([fmt, label]) =>
    `<a class="btn small" href="/api/reports/${encodeURIComponent(rep.id)}/export?format=${encodeURIComponent(fmt)}" download>${label}</a>`).join("");
  const saved = $("#hc-saved");
  if (rep.saved_files) setStatus(saved, `Saved automatically to:\n${rep.saved_files.map(esc).join("\n")}`);
  else if (rep.save_error) setStatus(saved, esc(rep.save_error), true);
  else setStatus(saved, "Download the report in the format you need. The HTML report prints neatly to PDF.");

  const tabs = Object.keys(DETAIL_TAB).filter((k) => k in rep || (k === "devices" && rep.network));
  $("#hc-details").innerHTML = tabs.length
    ? `Full details: ${tabs.map((k) => `<button class="link" data-goto="${DETAIL_TAB[k][0]}">${DETAIL_TAB[k][1]}</button>`).join(" · ")}`
    : "";
  $("#hc-results").hidden = false;
}

$("#hc-filters").addEventListener("click", (e) => {
  if (!e.target.dataset.filter) return;
  hcFilter = e.target.dataset.filter;
  renderRecs();
});
$("#hc-details").addEventListener("click", (e) => { if (e.target.dataset.goto) showTab(e.target.dataset.goto); });

function startDiagnosis(mode) {
  const buttons = document.querySelectorAll("[data-diagnose]");
  buttons.forEach((b) => { b.disabled = true; });
  $("#hc-results").hidden = true;
  $("#hc-progress").hidden = false;
  $("#hc-progress-title").textContent = mode === "full" ? "Running full scan…" : "Running quick scan…";
  let steps = [];
  const step = (id) => steps.find((st) => st.id === id);
  const update = (id, patch) => { Object.assign(step(id), patch); renderSteps(steps); };

  resetSpeed();
  if (mode === "full") { hosts.clear(); renderHosts(); }

  stream(`/api/diagnose?mode=${mode}`, (ev) => {
    if (ev.type === "plan") {
      steps = ev.steps.map((st) => ({ ...st, status: "pending", detail: "" }));
      renderSteps(steps);
    } else if (ev.type === "step") {
      update(ev.step, { status: ev.status, ...(ev.status === "running" ? { detail: "Starting…" } : {}) });
    } else if (ev.type === "step_event") {
      const e = ev.event;
      if (e.type === "error") { update(ev.step, { detail: e.message }); return; }
      if (ev.step === "speed") {
        const line = applySpeedEvent(e);
        if (line) update("speed", { detail: line });
      } else if (ev.step === "wifi") {
        if (e.wifi.error) update("wifi", { detail: e.wifi.error.split("\n")[0] });
        else { renderWifi(e.wifi); update("wifi", { detail: `${e.wifi.networks.length} access points found` }); }
      } else if (ev.step === "devices") {
        if (e.type === "start") update("devices", { detail: `Scanning ${e.network}…` });
        if (e.type === "host") { hosts.set(e.host.ip, { ...hosts.get(e.host.ip), ...e.host }); renderHosts(); }
        if (e.type === "progress") update("devices", { detail: `${e.done} / ${e.total} addresses checked · ${hosts.size} devices found` });
        if (e.type === "done") update("devices", { detail: `${e.hosts_found} devices found on ${e.network}` });
      } else if (ev.step === "ports") {
        const h = hosts.get(e.ip);
        if (h) { h.ports = e.ports; renderHosts(); }
        update("ports", { detail: `${e.done} / ${e.total} devices checked` });
      }
    } else if (ev.type === "report") {
      $("#hc-progress-title").textContent = `${mode === "full" ? "Full" : "Quick"} scan finished in ${ev.report.duration_s}s`;
      renderReport(ev.report);
    } else if (ev.type === "error") {
      $("#hc-progress-title").textContent = `Scan failed: ${ev.message}`;
    }
  }, (err) => {
    buttons.forEach((b) => { b.disabled = false; });
    if (err) $("#hc-progress-title").textContent = err.message;
  });
}
document.querySelectorAll("[data-diagnose]").forEach((b) => b.addEventListener("click", () => startDiagnosis(b.dataset.diagnose)));
