"use strict";
// Wi-Fi Monitor: live signal graph per access point, roaming events and a room-by-room survey.

const mon = { samples: [], bssids: [], events: [], marks: [], run: null };

// Live signal dial beside the graph: −90 to −30 dBm, coloured by signal quality.
const signalDial = createDial($("#mon-dial"), { ticks: [-90, -80, -70, -60, -50, -40, -30] });
function setSignalDial(s) {
  if (!s) return signalDial.set({ text: "–", unit: "dBm", label: "Ready" });
  if (!s.connected || s.signal_dbm == null) return signalDial.set({ text: "–", unit: "dBm", label: "Not connected", tone: "critical" });
  const q = quality(s.signal_dbm);
  signalDial.set({ value: s.signal_dbm, text: String(s.signal_dbm), unit: `dBm · ${s.band || ""} ch ${s.channel ?? "?"}`, label: q.label, tone: q.cls });
}
setSignalDial(null);
chartRedrawers.push(() => { if (!$("#tab-monitor").hidden) signalChart(); });
const fmtT = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

/** Fixed color slot per access point, in the order first seen (never re-assigned). */
function apSlot(bssid) {
  const key = bssid || "current";
  let i = mon.bssids.indexOf(key);
  if (i < 0) { mon.bssids.push(key); i = mon.bssids.length - 1; }
  return i < 8 ? String(i + 1) : "other";
}
const apLabel = (key) => (key === "current" ? "Current access point" : key);

function locationAt(t) {
  let label = null;
  for (const m of mon.marks) if (m.t <= t) label = m.label;
  return label;
}

function signalChart() {
  const container = $("#mon-chart");
  const W = Math.max(320, container.clientWidth || 760), H = W < 520 ? 260 : 296, m = { t: 38, r: W < 520 ? 72 : 92, b: 28, l: 44 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const last = mon.samples.length ? mon.samples[mon.samples.length - 1].t : 0;
  const xMax = Math.max(60, Math.ceil(last / 30) * 30);
  const yMin = -95, yMax = -25;
  const sx = (t) => m.l + (t / xMax) * iw;
  const sy = (d) => m.t + ih - ((Math.min(yMax, Math.max(yMin, d)) - yMin) / (yMax - yMin)) * ih;

  container.innerHTML = "";
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Wi-Fi signal strength over time" }, container);
  for (let d = -90; d <= -30; d += 10) {
    svgEl("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: sy(d), y2: sy(d) }, svg);
    svgEl("text", { class: "axis-label", x: m.l - 6, y: sy(d) + 4, "text-anchor": "end" }, svg).textContent = d;
  }
  svgEl("text", { class: "axis-label", x: m.l - 6, y: sy(-25) + 4, "text-anchor": "end" }, svg).textContent = "dBm";
  for (const [d, label] of [[-67, "Good for calls"], [-75, "Weak"]]) {
    svgEl("line", { class: "ref", x1: m.l, x2: m.l + iw, y1: sy(d), y2: sy(d) }, svg);
    svgEl("text", { class: "ref-label", x: m.l + iw + 6, y: sy(d) + 4 }, svg).textContent = label;
  }
  const step = xMax <= 120 ? 15 : xMax <= 600 ? 60 : 300;
  for (let t = 0; t <= xMax; t += step) {
    svgEl("text", { class: "axis-label", x: sx(t), y: H - 8, "text-anchor": "middle" }, svg).textContent = fmtT(t);
  }

  // Markers: locations (solid) and roams/disconnects (dashed).
  const markers = [
    ...mon.marks.map((mk) => ({ t: mk.t, label: mk.label, cls: "marker" })),
    ...mon.events.filter((e) => e.kind === "roam" || e.kind === "disconnect").map((e) => ({ t: e.t, label: e.kind === "roam" ? "Roam" : "Lost", cls: "marker roam" })),
  ];
  // Labels go on two rows; each takes the first row where it doesn't collide with the previous label.
  const rowEnd = [-Infinity, -Infinity];
  markers.sort((a, b) => a.t - b.t);
  for (const mk of markers) {
    const x = sx(mk.t), text = mk.label.length > 16 ? mk.label.slice(0, 15) + "…" : mk.label;
    const row = x > rowEnd[0] ? 0 : x > rowEnd[1] ? 1 : 0;
    rowEnd[row] = x + text.length * 6.5 + 8;
    svgEl("line", { class: mk.cls, x1: x, x2: x, y1: m.t - (row ? 4 : 18), y2: m.t + ih }, svg);
    svgEl("text", { class: "marker-label", x: x + 3, y: row ? m.t - 6 : m.t - 22 }, svg).textContent = text;
  }

  // One path segment per uninterrupted stretch on the same access point.
  let seg = [], segSlot = null;
  const flush = () => {
    if (seg.length) {
      const d = seg.map((s, i) => `${i ? "L" : "M"}${sx(s.t).toFixed(1)},${sy(s.signal_dbm).toFixed(1)}`).join("");
      svgEl("path", { class: `sig s-${segSlot}`, d: seg.length > 1 ? d : `${d}h0.1` }, svg);
    }
    seg = [];
  };
  for (const s of mon.samples) {
    if (!s.connected || s.signal_dbm == null) { flush(); continue; }
    const slot = apSlot(s.bssid);
    if (slot !== segSlot) { if (seg.length) seg.push(s); flush(); segSlot = slot; }
    seg.push(s);
  }
  flush();

  const dot = svgEl("circle", { class: "dot", r: 4, visibility: "hidden" }, svg);
  const hit = svgEl("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih }, svg);
  hit.addEventListener("mousemove", (e) => {
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const t = ((pt.matrixTransform(svg.getScreenCTM().inverse()).x - m.l) / iw) * xMax;
    const pts = mon.samples.filter((s) => s.connected && s.signal_dbm != null);
    if (!pts.length) return;
    const s = pts.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
    dot.setAttribute("cx", sx(s.t)); dot.setAttribute("cy", sy(s.signal_dbm)); dot.setAttribute("visibility", "visible");
    const q = quality(s.signal_dbm);
    showTip(`${fmtT(s.t)}${s.loc ? ` · ${esc(s.loc)}` : ""}<br><b>${s.signal_dbm} dBm</b> <span class="t-${q.cls}">${q.label}</span><br>
      <i class="key key-${apSlot(s.bssid)}"></i> ${esc(apLabel(s.bssid || "current"))} · ${esc(s.band || "")} ch ${s.channel ?? "?"}`, e.clientX, e.clientY);
  });
  hit.addEventListener("mouseleave", () => { dot.setAttribute("visibility", "hidden"); hideTip(); });

  $("#mon-legend").innerHTML = mon.bssids.length > 1 || mon.bssids[0] !== "current"
    ? mon.bssids.map((b) => `<span><i class="key key-${apSlot(b === "current" ? null : b)}"></i><span class="mono">${esc(apLabel(b))}</span></span>`).join("")
    : "";
}

function renderMonTiles(s) {
  if (!s.connected) {
    $("#mon-tiles").innerHTML = tile("Status", "Not connected", "Waiting for a Wi-Fi connection…", "small");
    return;
  }
  const q = quality(s.signal_dbm);
  $("#mon-tiles").innerHTML = [
    tile("Network", s.redacted ? "Hidden by macOS" : esc(s.ssid || "(hidden)"), esc(s.radio || ""), "small"),
    tile("Access point", s.bssid ? `<span class="mono">${esc(s.bssid)}</span>` : "Hidden by OS", `${esc(s.band || "?")} · channel ${s.channel ?? "?"}`, "small"),
    tile("Signal", `${s.signal_dbm ?? "?"}<small>dBm</small>`, `<span class="t-${q.cls}">${q.label}</span>${s.signal_percent != null ? ` · ${s.signal_percent}%` : ""}`),
    tile("Link rate", s.rx_rate_mbps || s.tx_rate_mbps ? `${Math.round(s.rx_rate_mbps || s.tx_rate_mbps)}<small>Mbps</small>` : "–", "Negotiated Wi-Fi speed, not internet speed"),
  ].join("");
}

function renderSurvey() {
  const groups = new Map();
  for (const s of mon.samples) {
    if (!s.loc || !s.connected || s.signal_dbm == null) continue;
    if (!groups.has(s.loc)) groups.set(s.loc, { vals: [], aps: new Set() });
    groups.get(s.loc).vals.push(s.signal_dbm);
    groups.get(s.loc).aps.add(s.bssid || "current");
  }
  const rows = [...groups.entries()].map(([loc, g]) => {
    const avg = Math.round(g.vals.reduce((a, b) => a + b, 0) / g.vals.length);
    return { loc, avg, min: Math.min(...g.vals), max: Math.max(...g.vals), n: g.vals.length, aps: [...g.aps] };
  });
  $("#mon-survey").innerHTML = rows.map((r) => {
    const q = quality(r.avg);
    return `<tr>
      <td><b>${esc(r.loc)}</b></td>
      <td><span class="t-${q.cls}">${SEV[q.cls].icon} ${q.cls === "critical" ? "Weak spot" : q.label}</span></td>
      <td class="num">${r.avg} dBm</td><td class="num">${r.min} dBm</td><td class="num">${r.max} dBm</td>
      <td>${r.aps.map((b) => `<span class="badge"><i class="key key-${apSlot(b === "current" ? null : b)}"></i> ${esc(b === "current" ? "current" : b.slice(-8))}</span>`).join("")}</td>
      <td class="num">${r.n}</td></tr>`;
  }).join("") || `<tr><td colspan="7" class="empty">Mark locations while monitoring to build a survey.</td></tr>`;
}

const EVENT_TAG = { roam: ["Roam", "good"], sticky: ["Sticky", "warning"], disconnect: ["Lost", "critical"], reconnect: ["Back", "good"], network_change: ["Network", "info"], mark: ["Location", "info"] };

function addMonEvent(ev) {
  mon.events.push(ev);
  const [label, cls] = EVENT_TAG[ev.kind] || ["Event", "info"];
  const tone = ev.kind === "roam" && ev.good === false ? "warning" : cls;
  const list = $("#mon-events");
  if (list.querySelector(".sub")) list.innerHTML = "";
  list.insertAdjacentHTML("afterbegin", `<li><span class="when">${fmtT(ev.t)}</span><span class="tag ${tone}">${label}</span><span>${esc(ev.message)}</span></li>`);
}

function renderAps(ev) {
  if (ev.error) { $("#mon-aps").innerHTML = `<tr><td colspan="4" class="empty">${esc(ev.error)}</td></tr>`; return; }
  $("#mon-aps-note").textContent = `${ev.aps.length} for this network · ${ev.nearby_total} nearby in total`;
  $("#mon-aps").innerHTML = ev.aps.map((a) => {
    const q = quality(a.signal_dbm);
    return `<tr><td class="mono">${esc(a.bssid || "–")} ${a.is_current ? `<span class="badge accent">Connected</span>` : ""}</td>
      <td>${esc(a.band || "–")}</td><td class="num">${a.channel ?? "–"}</td>
      <td><span class="t-${q.cls}">${q.label}</span> <span class="sub" style="display:inline">${a.signal_dbm ?? "?"} dBm</span></td></tr>`;
  }).join("") || `<tr><td colspan="4" class="empty">No access points for this network in the latest scan.</td></tr>`;
}

$("#mon-form").addEventListener("submit", (e) => {
  e.preventDefault();
  Object.assign(mon, { samples: [], bssids: [], events: [], marks: [], noBssidNote: false });
  $("#mon-loc-notice").hidden = true;
  $("#mon-events").innerHTML = `<li class="sub">Roams, disconnects and sticky-connection warnings appear here.</li>`;
  renderSurvey();
  signalChart();
  setSignalDial(null);
  $("#mon-start").hidden = true;
  $("#mon-stop").hidden = false;
  $("#mon-mark").disabled = false;
  const status = $("#mon-status");
  setStatus(status, "Monitoring… move around to see how the signal changes.");
  let failed = false;
  mon.run = stream(`/api/wifi/monitor?interval=${$("#mon-interval").value}`, (ev) => {
    if (ev.type === "sample") {
      ev.loc = locationAt(ev.t);
      mon.samples.push(ev);
      renderMonTiles(ev);
      setSignalDial(ev);
      signalChart();
      renderSurvey();
      $("#mon-export").disabled = false;
      if (ev.connected && (ev.redacted || !ev.bssid) && !mon.noBssidNote) {
        mon.noBssidNote = true;
        if (ev.redacted) {
          api("/api/macos/location").then((loc) => showLocationNotice($("#mon-loc-notice"), loc, () => {
            mon.run?.stop();
            $("#mon-start").click();
          })).catch(() => {});
          setStatus(status, "Monitoring… signal works, but macOS is hiding the network name and access point, so roaming can't be tracked yet.");
        } else {
          setStatus(status, "Monitoring… your OS hides the access point's BSSID, so roaming can't be tracked.");
        }
      }
    } else if (ev.type === "event") {
      addMonEvent(ev);
      signalChart();
    } else if (ev.type === "aps") {
      renderAps(ev);
    } else if (ev.type === "error") {
      failed = true;
      setStatus(status, esc(ev.message), true);
    }
  }, (err) => {
    $("#mon-start").hidden = false;
    $("#mon-stop").hidden = true;
    $("#mon-mark").disabled = true;
    mon.run = null;
    if (err && !failed) setStatus(status, esc(err.message), true);
  });
});

$("#mon-stop").addEventListener("click", () => {
  mon.run?.stop();
  setStatus($("#mon-status"), `Stopped after ${fmtT(mon.samples.at(-1)?.t || 0)}. Export the samples or start again.`);
});

$("#mon-mark").addEventListener("click", () => {
  const label = $("#mon-location").value.trim() || `Spot ${mon.marks.length + 1}`;
  const t = mon.samples.at(-1)?.t ?? 0;
  mon.marks.push({ t, label });
  addMonEvent({ kind: "mark", t, message: `Now at: ${label}` });
  $("#mon-location").value = "";
  signalChart();
});
$("#mon-location").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); if (!$("#mon-mark").disabled) $("#mon-mark").click(); }
});

$("#mon-export").addEventListener("click", () => {
  const cols = ["t", "location", "connected", "ssid", "bssid", "signal_dbm", "signal_percent", "band", "channel", "rx_rate_mbps"];
  const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = [cols.join(","), ...mon.samples.map((s) => cols.map((c) => cell(c === "location" ? s.loc : s[c])).join(","))].join("\n");
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })),
    download: `subnetry-wifi-monitor-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`,
  });
  a.click();
  URL.revokeObjectURL(a.href);
});

signalChart();
