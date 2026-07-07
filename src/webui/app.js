/* Scr33nX web UI — shell logic (Phase 2).
   Pull model: poll the Python bridge once a second and diff-render.
   window.pywebview.api → Bridge methods in app_web.py. */

"use strict";

let API = null;
let logSeq = 0;
let fetching = false;
const SITE_LABEL = { chaturbate: "CHATURBATE", stripchat: "STRIPCHAT",
                     camsoda: "CAMSODA", myfreecams: "MYFREECAMS" };
const SITE_CHIP = { CB: "chaturbate", SC: "stripchat", CS: "camsoda", MFC: "myfreecams" };
const STATUS_TXT = { recording: "RECORDING", online: "ONLINE", offline: "OFFLINE",
                     private: "PRIVATE / TICKET", checking: "CHECKING…", error: "ERROR" };
const collapsed = new Set();     // site keys the user collapsed
const rowEls = new Map();        // model key → row element

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ── boot ── */
function start() {
  if (API) return;
  if (!(window.pywebview && window.pywebview.api)) return;
  API = window.pywebview.api;
  tick();
  setInterval(tick, 1000);
}
window.addEventListener("pywebviewready", start);
document.addEventListener("DOMContentLoaded", () => setTimeout(start, 400));

async function tick() {
  if (!API || fetching) return;
  fetching = true;
  try {
    const s = await API.state(logSeq);
    render(s);
  } catch (e) { /* window closing / bridge gone — skip this tick */ }
  fetching = false;
}

/* ── render ── */
function render(s) {
  $("ver").textContent = "v" + s.version;

  const up = $("update-pill");
  if (s.update) {
    up.hidden = false;
    up.textContent = `● Update available (${s.update})`;
  }

  $("m-down").textContent = s.meters.down.toFixed(1);
  $("m-up").textContent = s.meters.up.toFixed(1);

  const r = s.monitoring.recorder, v = s.monitoring.saved;
  const stTxt = r && v ? "MONITORING (R+S)" : r ? "RECORDER ACTIVE"
              : v ? "SCANNER ACTIVE" : "IDLE";
  $("hdr-status").textContent = stTxt;
  $("hdr-dot").style.background = (r || v) ? "var(--go-bright)" : "var(--text-3)";

  const mb = $("btn-monitor");
  mb.textContent = r ? "⏹ Stop Monitor" : "▶ Start Monitor";
  mb.classList.toggle("go", !r);
  mb.classList.toggle("stop", r);

  renderDash(s.dash);
  renderRows(s.models);
  renderLog(s.log, s.log_seq);
  $("saved-count").textContent = s.saved_count;

  window._active = s.active_recordings;   // for terminate/quit confirms
}

function renderDash(d) {
  $("dash-live").textContent = `${d.all.recording} LIVE`;
  $("dash-total").textContent = `${d.all.total} models`;
  let html = "";
  for (const [chip, counts] of Object.entries(d.sites)) {
    if (!counts.total) continue;
    const recW = Math.max(counts.recording / counts.total * 100, counts.recording ? 6 : 0);
    const onW = Math.max(counts.online / counts.total * 100, counts.online ? 6 : 0);
    html += `
      <div class="dash-row"><span class="chip">${chip}</span>
        <span class="c ${counts.recording ? "rec" : "dim"}">${counts.recording}</span>
        <span class="c ${counts.online ? "on" : "dim"}">${counts.online}</span>
        <span class="c off">${counts.offline}</span></div>
      <div class="bar"><i class="rec" style="width:${recW}%"></i><i class="on" style="width:${onW}%"></i></div>`;
  }
  html += `
    <div class="dash-row all"><span class="chip">ALL</span>
      <span class="c ${d.all.recording ? "rec" : "dim"}">${d.all.recording}</span>
      <span class="c ${d.all.online ? "on" : "dim"}">${d.all.online}</span>
      <span class="c off">${d.all.offline}</span></div>`;
  $("dash-rows").innerHTML = html;
}

function rowHtml(m) {
  return `
    <span class="cb"></span>
    <span class="name">${esc(m.name)}</span>
    <span class="stars"><b>${"★".repeat(m.rank)}</b>${"☆".repeat(5 - m.rank)}</span>
    <span class="st ${m.status}"><span class="dot"></span><span class="st-txt">${STATUS_TXT[m.status] || m.status}</span></span>
    <span class="file">${esc(m.file || "—")}</span>
    <span class="size">${esc(m.size || "—")}</span>
    <span class="switch ${m.auto ? "on" : ""}" data-key="${esc(m.key)}" title="Auto-record"></span>
    <span class="saved-check ${m.saved ? "" : "no"}">${m.saved ? "✓" : "—"}</span>`;
}

function renderRows(models) {
  const container = $("rows");
  const bySite = new Map();
  for (const m of models) {
    if (!bySite.has(m.site)) bySite.set(m.site, []);
    bySite.get(m.site).push(m);
  }

  // Structure change (site set / model set / order) → rebuild; else in-place.
  const sig = models.map(m => m.key).join("|");
  if (container.dataset.sig !== sig) {
    container.dataset.sig = sig;
    rowEls.clear();
    container.innerHTML = "";
    for (const [site, list] of bySite) {
      const head = document.createElement("div");
      head.className = "g-site" + (collapsed.has(site) ? " closed" : "");
      head.innerHTML = `<button class="tog" data-site="${esc(site)}"></button>` +
                       `${SITE_LABEL[site] || site.toUpperCase()}<small>${list.length} models</small>`;
      container.appendChild(head);
      const body = document.createElement("div");
      body.className = "site-rows" + (collapsed.has(site) ? " collapsed" : "");
      body.dataset.site = site;
      for (const m of list) {
        const el = document.createElement("div");
        el.className = "row gr" + (m.status === "recording" ? " recording" : "");
        el.dataset.key = m.key;
        el.innerHTML = rowHtml(m);
        body.appendChild(el);
        rowEls.set(m.key, el);
      }
      container.appendChild(body);
    }
    return;
  }

  for (const m of models) {                    // in-place field updates
    const el = rowEls.get(m.key);
    if (!el) continue;
    el.classList.toggle("recording", m.status === "recording");
    const st = el.querySelector(".st");
    st.className = `st ${m.status}`;
    st.querySelector(".st-txt").textContent = STATUS_TXT[m.status] || m.status;
    setText(el, ".file", m.file || "—");
    setText(el, ".size", m.size || "—");
    const sw = el.querySelector(".switch");
    sw.classList.toggle("on", m.auto);
    const stars = el.querySelector(".stars");
    const starsHtml = `<b>${"★".repeat(m.rank)}</b>${"☆".repeat(5 - m.rank)}`;
    if (stars.innerHTML !== starsHtml) stars.innerHTML = starsHtml;
    const sc = el.querySelector(".saved-check");
    sc.classList.toggle("no", !m.saved);
    sc.textContent = m.saved ? "✓" : "—";
  }
}

function setText(root, selector, value) {
  const el = root.querySelector(selector);
  if (el.textContent !== value) el.textContent = value;
}

function renderLog(entries, seq) {
  if (!entries.length) { logSeq = seq; return; }
  const pane = $("logpane");
  const stick = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 24;
  for (const e of entries) {
    const div = document.createElement("div");
    div.innerHTML = `<span class="t">[${esc(e.t)}]</span> <span class="k-${esc(e.k)}">${esc(e.m)}</span>`;
    pane.appendChild(div);
  }
  while (pane.childElementCount > 800) pane.firstElementChild.remove();
  if (stick) pane.scrollTop = pane.scrollHeight;
  logSeq = seq;
}

/* ── interactions ── */
document.addEventListener("click", (e) => {
  const tog = e.target.closest(".g-site .tog");
  if (tog) {
    const site = tog.dataset.site;
    const head = tog.closest(".g-site");
    const body = head.nextElementSibling;
    if (collapsed.has(site)) collapsed.delete(site); else collapsed.add(site);
    head.classList.toggle("closed", collapsed.has(site));
    body.classList.toggle("collapsed", collapsed.has(site));
    return;
  }
  const sw = e.target.closest(".switch[data-key]");
  if (sw && API) {
    const on = !sw.classList.contains("on");
    sw.classList.toggle("on", on);          // optimistic; next tick confirms
    API.set_auto(sw.dataset.key, on);
  }
});

document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
  document.querySelector(".tab.active").classList.remove("active");
  t.classList.add("active");
  document.querySelector(".panel.active").classList.remove("active");
  document.querySelector(`.panel[data-panel="${t.dataset.tab}"]`).classList.add("active");
}));

$("btn-add").addEventListener("click", addModel);
$("add-name").addEventListener("keydown", (e) => { if (e.key === "Enter") addModel(); });
async function addModel() {
  if (!API) return;
  const raw = $("add-name").value.trim();
  if (!raw) { toast("Enter a model username or link.", true); return; }
  const res = await API.add_model(raw, $("add-site").value);
  if (res.ok) {
    $("add-name").value = "";
    toast(`Added ${res.name} (${res.site})`);
  } else {
    toast(res.error || "Could not add model.", true);
  }
}

$("btn-monitor").addEventListener("click", async () => {
  if (!API) return;
  const starting = $("btn-monitor").classList.contains("go");
  await API.set_monitor("recorder", starting);
  tick();
});

$("btn-term").addEventListener("click", () => {
  const n = window._active || 0;
  if (n > 0) {
    modal("Terminate Scr33nX",
          `${n} recording(s) still active.\n\nForce-terminate now? Their final segments will be dropped.`,
          "Terminate", () => API.terminate());
  } else {
    API.terminate();
  }
});

$("btn-clearlog").addEventListener("click", () => { $("logpane").innerHTML = ""; });

/* Called from Python when the window X is pressed while recording. */
window.UI = {
  confirmQuit(n) {
    modal("Quit Scr33nX",
          `${n} recording(s) still active.\n\nQuit anyway? Recordings will be stopped.`,
          "Quit", () => API.quit());
  }
};

/* ── modal / toast helpers ── */
function modal(title, text, okLabel, onOk) {
  $("modal-title").textContent = title;
  $("modal-text").textContent = text;
  $("modal-ok").textContent = okLabel;
  $("modal").hidden = false;
  $("modal-ok").onclick = () => { $("modal").hidden = true; onOk(); };
  $("modal-cancel").onclick = () => { $("modal").hidden = true; };
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("modal").hidden = true;
});

let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}
