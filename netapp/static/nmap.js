"use strict";
// Port Scanner: runs Nmap on the server and shows live progress, per-device results and findings.

const RISKY = new Set([21, 23, 1883, 3389, 5900, 6379, 27017]);
let nmapStatus = null;
let nmapRun = null;
const nmapLive = new Map(); // ip -> Set of ports discovered so far

async function loadNmap() {
  if (nmapStatus) return;
  try {
    nmapStatus = await api("/api/nmap/status");
  } catch (err) {
    setStatus($("#nmap-status"), esc(err.message), true);
    return;
  }
  const sel = $("#nmap-profile");
  sel.innerHTML = nmapStatus.profiles.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join("");
  const describe = () => {
    $("#nmap-profile-desc").textContent = nmapStatus.profiles.find((p) => p.id === sel.value)?.description || "";
  };
  sel.addEventListener("change", describe);
  describe();
  $("#nmap-os-note").textContent = nmapStatus.admin ? "" : "(needs admin/root)";
  if (!nmapStatus.installed) {
    const box = $("#nmap-missing");
    box.hidden = false;
    box.innerHTML = `<h2>Nmap isn't installed</h2><p>${esc(nmapStatus.install_help)}</p>`;
    $("#nmap-start").disabled = true;
  }
  const o = await loadOverview();
  if (!$("#nmap-target").value && o?.networks?.length) $("#nmap-target").value = o.networks[0].network;
  syncPublic();
}
loaders.nmap = loadNmap;

function renderNmapLive() {
  $("#nmap-results").innerHTML = [...nmapLive.entries()].map(([ip, ports]) => `
    <div class="card host-card"><h3 class="mono">${esc(ip)}</h3>
      <div class="meta">Open so far: ${[...ports].sort((a, b) => a - b).map((p) => `<span class="badge">${p}</span>`).join("")}</div></div>`).join("");
}

function renderNmapResult(r) {
  $("#nmap-findings").innerHTML = r.findings.length ? `<h2>Findings</h2>${r.findings.map(recCard).join("")}` : "";
  if (!r.hosts.length) {
    $("#nmap-results").innerHTML = `<p class="muted">No hosts responded.</p>`;
    return;
  }
  const isDiscovery = /(^|\s)-sn(\s|$)/.test(r.args || "");
  $("#nmap-results").innerHTML = `<h2>${r.hosts.length} host${r.hosts.length === 1 ? "" : "s"} up</h2>` + r.hosts.map((h) => {
    const meta = [
      h.hostnames.length && esc(h.hostnames.join(", ")),
      h.mac && `MAC <span class="mono">${esc(h.mac)}</span>${h.vendor ? ` (${esc(h.vendor)})` : ""}`,
      h.os.length && `OS guess: ${esc(h.os[0].name)} (${h.os[0].accuracy}%)`,
    ].filter(Boolean).join(" · ");
    const rows = h.ports.map((p) => `<tr>
        <td class="mono">${p.port}/${esc(p.protocol)}</td>
        <td>${esc(p.service || "unknown")}${RISKY.has(p.port) ? ` <span class="badge t-critical">Risky</span>` : ""}</td>
        <td>${esc(p.product || "–")}${p.scripts.length ? `<details><summary>${p.scripts.length} script result(s)</summary>${p.scripts.map((s) => `<pre><b>${esc(s.id)}</b>\n${esc(s.output)}</pre>`).join("")}</details>` : ""}</td>
        <td>${esc(p.state)}</td></tr>`).join("");
    const table = isDiscovery ? "" : rows
      ? `<div class="table-wrap"><table><thead><tr><th>Port</th><th>Service</th><th>Software / version</th><th>State</th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="sub">No open ports found in the scanned range.</p>`;
    const tcpOpen = h.ports.filter((p) => p.protocol === "tcp" && p.state === "open").map((p) => p.port);
    const urls = webUrls(h.ip, tcpOpen);
    const kind = deviceKind(tcpOpen);
    const title = urls.length
      ? `<a class="ip-link" href="${esc(urls[0].url)}" target="_blank" rel="noopener noreferrer" title="Open its web interface">${esc(h.ip)} ↗</a>`
      : esc(h.ip);
    const web = urls.length ? `<div class="web-links">${urls.map((u) => openLink(u, `Open :${u.port}`)).join("")}</div>` : "";
    return `<div class="card host-card"><h3 class="mono">${title}${kind ? ` <span class="badge">${kind}</span>` : ""}</h3>
      <div class="meta">${meta || "&nbsp;"}</div>${web}${table}</div>`;
  }).join("");
}

// Public targets (anything outside the private/LAN ranges, and hostnames) need explicit permission.
function looksPublic(target) {
  const t = target.trim();
  const m = t.match(/^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}(\/\d{1,2})?$/);
  if (!t) return false;
  if (!m) return !/^localhost$/i.test(t);  // a hostname: the server resolves and decides
  const [a, b] = [Number(m[1]), Number(m[2])];
  return !(a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254));
}
function syncPublic() {
  const pub = looksPublic($("#nmap-target").value);
  $("#nmap-auth-wrap").hidden = !pub;
  $("#nmap-public-note").hidden = !pub;
}
$("#nmap-target").addEventListener("input", syncPublic);
$("#nmap-myip").addEventListener("click", async () => {
  const btn = $("#nmap-myip");
  btn.disabled = true;
  try {
    $("#nmap-target").value = (await api("/api/public-ip")).ip;
    syncPublic();
  } catch (err) {
    setStatus($("#nmap-status"), `Couldn't get your public IP: ${esc(err.message)}`, true);
  }
  btn.disabled = false;
});

$("#nmap-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const status = $("#nmap-status"), bar = $("#nmap-bar");
  const params = new URLSearchParams({
    target: $("#nmap-target").value.trim(),
    profile: $("#nmap-profile").value,
    os_detect: $("#nmap-os").checked,
    scripts: $("#nmap-scripts").checked,
    authorized: $("#nmap-auth").checked,
  });
  if (looksPublic($("#nmap-target").value) && !$("#nmap-auth").checked) {
    setStatus($("#nmap-status"), "This is a public target. Only scan systems you own or have permission to test, then tick the box to confirm.", true);
    return;
  }
  $("#nmap-start").hidden = true;
  $("#nmap-stop").hidden = false;
  $("#nmap-findings").innerHTML = $("#nmap-results").innerHTML = $("#nmap-command").textContent = "";
  nmapLive.clear();
  bar.style.width = "0";
  setStatus(status, "Starting Nmap…");
  let failed = false;
  nmapRun = stream(`/api/nmap/scan?${params}`, (ev) => {
    if (ev.type === "start") $("#nmap-command").textContent = `$ ${ev.command}${ev.resolved ? `   (${ev.resolved})` : ""}`;
    if (ev.type === "progress") { bar.style.width = `${ev.percent}%`; setStatus(status, `${esc(ev.task)}: ${ev.percent.toFixed(0)}% done`); }
    if (ev.type === "task") setStatus(status, esc(ev.message));
    if (ev.type === "open_port") {
      if (!nmapLive.has(ev.ip)) nmapLive.set(ev.ip, new Set());
      nmapLive.get(ev.ip).add(ev.port);
      renderNmapLive();
    }
    if (ev.type === "result") {
      bar.style.width = "100%";
      setStatus(status, esc(ev.result.summary || "Scan complete."));
      renderNmapResult(ev.result);
    }
    if (ev.type === "error") { failed = true; setStatus(status, esc(ev.message), true); }
  }, (err) => {
    $("#nmap-start").hidden = false;
    $("#nmap-stop").hidden = true;
    nmapRun = null;
    if (err && !failed) setStatus(status, esc(err.message), true);
  });
});
$("#nmap-stop").addEventListener("click", () => {
  nmapRun?.stop();
  setStatus($("#nmap-status"), "Scan stopped.");
  $("#nmap-bar").style.width = "0";
});
