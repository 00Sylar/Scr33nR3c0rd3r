/* Scr33nX web UI — full client logic.
   Pull model: poll the Python bridge once a second and diff-render.
   window.pywebview.api → Bridge methods in app_web.py. */

"use strict";

/* ══ helpers ══ */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SITE_LABEL = { chaturbate: "CHATURBATE", stripchat: "STRIPCHAT",
                     camsoda: "CAMSODA", myfreecams: "MYFREECAMS" };
const STATUS_TXT = { recording: "RECORDING", online: "ONLINE", offline: "OFFLINE",
                     private: "PRIVATE / TICKET", checking: "CHECKING…", error: "ERROR" };
const STATUS_W = { recording: 0, online: 1, private: 2, checking: 3, error: 4, offline: 5 };
const ROW_H = 40;

function parseSize(s) {
  if (!s) return 0;
  const n = parseFloat(s) || 0;
  return s.includes("GB") ? n * 1024 : n;
}
function starsHtml(rank) {
  let h = "";
  for (let i = 1; i <= 5; i++)
    h += `<i data-i="${i}" class="${i <= rank ? "f" : ""}">${i <= rank ? "★" : "☆"}</i>`;
  return h;
}

/* ══ global state ══ */
let API = null;
let S = null;                       // last snapshot
let logSeq = 0, pipeSeq = 0;
let fetching = false;
let qualityOpts = [];               // [{label, height}]
const sel = { rec: new Set(), saved: new Set() };      // click-selection
const checked = { rec: new Set(), saved: new Set() };  // checkbox working set
const collapsed = { rec: new Set(), saved: new Set() };
const anchor = { rec: null, saved: null };
const sort = { rec: { col: "name", dir: 1 }, saved: { col: "name", dir: 1 } };
const filter = { rec: "", saved: "" };
const statusFilter = { rec: new Set(), saved: new Set() };   // empty = all
let savedCache = { version: -1, items: [] };
let savedDisplay = [];              // flattened (headers + rows) for virtualization
let recVisible = [];                // ordered visible keys (for shift ranges)
const rowEls = new Map();
let settingsLoaded = false;
let suppressClick = false;

/* ══ boot ══ */
function start() {
  if (API) return;
  if (!(window.pywebview && window.pywebview.api)) return;
  API = window.pywebview.api;
  API.quality_options().then(r => { qualityOpts = r.options || []; });
  tick();
  setInterval(tick, 1000);
}
window.addEventListener("pywebviewready", start);
document.addEventListener("DOMContentLoaded", () => setTimeout(start, 400));

async function tick() {
  if (!API || fetching) return;
  fetching = true;
  try {
    const s = await API.state(logSeq, pipeSeq);
    S = s;
    render(s);
  } catch (e) { /* bridge gone (closing) — skip */ }
  fetching = false;
}

/* ══ top-level render ══ */
function render(s) {
  $("ver").textContent = "v" + s.version;
  const up = $("update-pill");
  if (s.update) { up.hidden = false; up.textContent = `● Update available (${s.update})`; }
  $("lowdisk-pill").hidden = !s.low_disk;
  $("m-down").textContent = s.meters.down.toFixed(1);
  $("m-up").textContent = s.meters.up.toFixed(1);

  const r = s.monitoring.recorder, v = s.monitoring.saved;
  $("hdr-status").textContent = r && v ? "MONITORING (R+S)" : r ? "RECORDER ACTIVE"
                              : v ? "SCANNER ACTIVE" : "IDLE";
  $("hdr-dot").style.background = (r || v) ? "var(--go-bright)" : "var(--text-3)";

  setToggleBtn($("btn-monitor"), r, "▶ Start Monitor", "⏹ Stop Monitor");
  setToggleBtn($("btn-scanner"), v, "▶ Start Scanner", "⏹ Stop Scanner");

  renderDash(s.dash);
  renderRec(s.models);
  renderSaved();
  renderLog(s.log, s.log_seq);
  renderPipe(s.pipe);
  $("saved-count").textContent = `${s.saved_count.toLocaleString()} model(s)`;
  privacyApply(s.privacy);
}

function setToggleBtn(btn, on, offText, onText) {
  btn.textContent = on ? onText : offText;
  btn.classList.toggle("go", !on);
  btn.classList.toggle("stop", on);
}

function renderDash(d) {
  $("dash-live").textContent = `${d.all.recording} LIVE`;
  $("dash-total").textContent = `${d.all.total} models`;
  let html = "";
  for (const [chip, c] of Object.entries(d.sites)) {
    if (!c.total) continue;
    const rw = Math.max(c.recording / c.total * 100, c.recording ? 6 : 0);
    const ow = Math.max(c.online / c.total * 100, c.online ? 6 : 0);
    html += `
      <div class="dash-row"><span class="chip">${chip}</span>
        <span class="c ${c.recording ? "rec" : "dim"}">${c.recording}</span>
        <span class="c ${c.online ? "on" : "dim"}">${c.online}</span>
        <span class="c off">${c.offline}</span></div>
      <div class="bar"><i class="rec" style="width:${rw}%"></i><i class="on" style="width:${ow}%"></i></div>`;
  }
  html += `
    <div class="dash-row all"><span class="chip">ALL</span>
      <span class="c ${d.all.recording ? "rec" : "dim"}">${d.all.recording}</span>
      <span class="c ${d.all.online ? "on" : "dim"}">${d.all.online}</span>
      <span class="c off">${d.all.offline}</span></div>`;
  $("dash-rows").innerHTML = html;
}

/* ══ recorder table ══ */
function cmpModels(a, b, col, dir) {
  let x, y;
  switch (col) {
    case "rank":   x = a.rank; y = b.rank; break;
    case "status": x = STATUS_W[a.status]; y = STATUS_W[b.status]; break;
    case "size":   x = parseSize(a.size); y = parseSize(b.size); break;
    case "auto":   x = a.auto ? 0 : 1; y = b.auto ? 0 : 1; break;
    case "saved":  x = a.saved ? 0 : 1; y = b.saved ? 0 : 1; break;
    default:       x = a.name; y = b.name;
  }
  if (x < y) return -dir;
  if (x > y) return dir;
  return a.name < b.name ? -1 : 1;
}

function computeRecView(models) {
  const f = filter.rec.toLowerCase();
  const sf = statusFilter.rec;
  const list = models.filter(m =>
    (!f || m.name.includes(f)) && (sf.size === 0 || sf.has(m.status)));
  const bySite = new Map();
  for (const m of list) {
    if (!bySite.has(m.site)) bySite.set(m.site, []);
    bySite.get(m.site).push(m);
  }
  const { col, dir } = sort.rec;
  for (const arr of bySite.values()) arr.sort((a, b) => cmpModels(a, b, col, dir));
  return bySite;
}

function recRowHtml(m) {
  return `
    <span class="cb" data-cb="${esc(m.key)}"></span>
    <span class="name">${esc(m.name)}</span>
    <span class="stars" data-stars="${esc(m.key)}" data-rank="${m.rank}">${starsHtml(m.rank)}</span>
    <span class="st ${m.status}"><span class="dot"></span><span class="st-txt">${STATUS_TXT[m.status]}</span></span>
    <span class="file">${esc(m.file || "—")}</span>
    <span class="size">${esc(m.size || "—")}</span>
    <span class="switch ${m.auto ? "on" : ""}" data-auto="${esc(m.key)}" title="Auto-record"></span>
    <span class="saved-check ${m.saved ? "" : "no"}">${m.saved ? "✓" : "—"}</span>`;
}

function renderRec(models) {
  const bySite = computeRecView(models);
  recVisible = [];
  for (const [site, list] of bySite)
    if (!collapsed.rec.has(site)) recVisible.push(...list.map(m => m.key));

  updateSortArrows("rec-head", sort.rec);
  const container = $("rows");
  const sig = [...bySite.entries()].map(([s, l]) =>
    s + (collapsed.rec.has(s) ? "!" : ":") + l.map(m => m.key).join(",")).join("|");
  if (container.dataset.sig !== sig) {
    container.dataset.sig = sig;
    rowEls.clear();
    let html = "";
    for (const [site, list] of bySite) {
      const closed = collapsed.rec.has(site);
      html += `<div class="g-site ${closed ? "closed" : ""}">
        <button class="tog" data-tog="rec:${esc(site)}"></button>
        ${SITE_LABEL[site] || site.toUpperCase()}<small>${list.length} models</small></div>
        <div class="site-rows ${closed ? "collapsed" : ""}">`;
      for (const m of list)
        html += `<div class="row gr ${m.status === "recording" ? "recording" : ""}"
                      data-key="${esc(m.key)}" data-tab="rec">${recRowHtml(m)}</div>`;
      html += `</div>`;
    }
    container.innerHTML = html;
    container.querySelectorAll(".row").forEach(el => rowEls.set(el.dataset.key, el));
  }
  const byKey = new Map(models.map(m => [m.key, m]));
  for (const [key, el] of rowEls) {
    const m = byKey.get(key);
    if (!m) continue;
    el.classList.toggle("recording", m.status === "recording");
    el.classList.toggle("selected", sel.rec.has(key));
    const st = el.querySelector(".st");
    st.className = `st ${m.status}`;
    st.querySelector(".st-txt").textContent = STATUS_TXT[m.status];
    setText(el, ".file", m.file || "—");
    setText(el, ".size", m.size || "—");
    el.querySelector(".switch").classList.toggle("on", m.auto);
    const stars = el.querySelector(".stars");
    if (stars.dataset.rank !== String(m.rank)) {
      stars.dataset.rank = m.rank;
      stars.innerHTML = starsHtml(m.rank);
    }
    const sc = el.querySelector(".saved-check");
    sc.classList.toggle("no", !m.saved);
    sc.textContent = m.saved ? "✓" : "—";
    el.querySelector(".cb").classList.toggle("on", checked.rec.has(key));
  }
  updateSelLabel();
}

function setText(root, selector, value) {
  const el = root.querySelector(selector);
  if (el && el.textContent !== value) el.textContent = value;
}

function updateSelLabel() {
  const n = checked.rec.size;
  $("rec-sel").textContent = n ? `✓ ${n} checked`
    : (sel.rec.size ? `${sel.rec.size} selected` : "0 selected");
}

function updateSortArrows(headId, st) {
  document.querySelectorAll(`#${headId} .sortable`).forEach(el => {
    const on = el.dataset.col === st.col;
    el.classList.toggle("on", on);
    el.querySelector("i").textContent = on ? (st.dir > 0 ? "↑" : "↓") : "↕";
  });
}

/* ══ saved table (virtualized) ══ */
async function ensureSavedList() {
  if (!API) return;
  if (S && savedCache.version === S.saved_version) return;
  const r = await API.saved_list();
  savedCache = r;
  rebuildSavedDisplay();
}

function rebuildSavedDisplay() {
  const f = filter.saved.toLowerCase();
  const sf = statusFilter.saved;
  const stMap = (S && S.saved_status) || {};
  const items = [];
  for (const it of savedCache.items) {
    const stat = stMap[it.sid];
    const status = stat ? stat[0] : "offline";
    if (f && !it.name.toLowerCase().includes(f)) continue;
    if (sf.size && !sf.has(status)) continue;
    items.push({ ...it, status, file: stat ? stat[1] : "", size: stat ? stat[2] : "" });
  }
  const bySite = new Map();
  for (const it of items) {
    if (!bySite.has(it.site)) bySite.set(it.site, []);
    bySite.get(it.site).push(it);
  }
  const { col, dir } = sort.saved;
  savedDisplay = [];
  for (const [site, list] of bySite) {
    list.sort((a, b) => cmpModels(a, b, col, dir));
    savedDisplay.push({ head: site, count: list.length });
    if (!collapsed.saved.has(site)) savedDisplay.push(...list);
  }
  $("saved-viewport").style.height = (savedDisplay.length * ROW_H) + "px";
  renderSavedWindow();
}

function renderSaved() {
  ensureSavedList();
  rebuildSavedDisplay();      // statuses may have changed this tick
}

function savedRowHtml(it, top) {
  return `<div class="row gr gr-s ${it.status === "recording" ? "recording" : ""}
      ${sel.saved.has(it.sid) ? "selected" : ""}" style="top:${top}px"
      data-key="${esc(it.sid)}" data-tab="saved">
    <span class="cb ${checked.saved.has(it.sid) ? "on" : ""}" data-cb="${esc(it.sid)}"></span>
    <span class="name">${esc(it.name)}</span>
    <span class="stars" data-stars="${esc(it.sid)}" data-rank="${it.rank}">${starsHtml(it.rank)}</span>
    <span class="st ${it.status}"><span class="dot"></span><span class="st-txt">${STATUS_TXT[it.status]}</span></span>
    <span class="file">${esc(it.file || "—")}</span>
    <span class="size">${esc(it.size || "—")}</span>
  </div>`;
}

function renderSavedWindow() {
  updateSortArrows("saved-head", sort.saved);
  const wrap = $("saved-wrap");
  const vp = $("saved-viewport");
  const first = Math.max(0, Math.floor(wrap.scrollTop / ROW_H) - 4);
  const last = Math.min(savedDisplay.length,
                        first + Math.ceil(wrap.clientHeight / ROW_H) + 8);
  let html = "";
  for (let i = first; i < last; i++) {
    const it = savedDisplay[i];
    const top = i * ROW_H;
    if (it.head !== undefined) {
      const closed = collapsed.saved.has(it.head);
      html += `<div class="g-site ${closed ? "closed" : ""}" style="top:${top}px">
        <button class="tog" data-tog="saved:${esc(it.head)}"></button>
        ${SITE_LABEL[it.head] || it.head.toUpperCase()}<small>${it.count} models</small></div>`;
    } else {
      html += savedRowHtml(it, top);
    }
  }
  vp.innerHTML = html;
}
$("saved-wrap").addEventListener("scroll", () => requestAnimationFrame(renderSavedWindow));

/* ══ log panes ══ */
function renderLog(entries, seq) {
  appendLog($("logpane"), entries);
  logSeq = seq;
}
function appendLog(pane, entries) {
  if (!entries || !entries.length) return;
  const stick = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 24;
  for (const e of entries) {
    const div = document.createElement("div");
    div.innerHTML = `<span class="t">[${esc(e.t)}]</span> <span class="k-${esc(e.k)}">${esc(e.m)}</span>`;
    pane.appendChild(div);
  }
  while (pane.childElementCount > 800) pane.firstElementChild.remove();
  if (stick) pane.scrollTop = pane.scrollHeight;
}

/* ══ pipeline ══ */
function renderPipe(p) {
  const btn = $("b-pipe");
  btn.textContent = p.running ? "⏹ Stop Pipeline" : "▶ Start Pipeline";
  btn.classList.toggle("go", !p.running);
  btn.classList.toggle("stop", p.running);
  const el = $("pipe-state");
  let cls = "offline", txt = "STOPPED";
  if (p.state === "starting") { cls = "checking"; txt = "STARTING"; }
  else if (p.state === "stopping") { cls = "private"; txt = "STOPPING"; }
  else if (p.state === "error") { cls = "error"; txt = "ERROR"; }
  else if (p.running) {
    if (p.convert && p.upload) { cls = "online"; txt = "CONVERTING & UPLOADING"; }
    else if (p.convert) { cls = "online"; txt = "CONVERTING"; }
    else if (p.upload) { cls = "online"; txt = "UPLOADING"; }
    else { cls = "checking"; txt = "STAND BY"; }
  }
  el.className = `st ${cls}`;
  el.innerHTML = `<span class="dot"></span>${txt}`;
  $("stage-convert").classList.toggle("on", p.convert);
  $("stage-upload").classList.toggle("on", p.upload);
  $("pipe-lines").textContent = p.lines.join("\n");
  appendLog($("pipe-log"), p.log);
  pipeSeq = p.log_seq;
}

let pipeLoaded = false;
async function loadPipeFields() {
  if (pipeLoaded || !API) return;
  pipeLoaded = true;
  const p = await API.pipeline_get();
  $("p-api-id").value = p.api_id || "";
  $("p-api-hash").value = p.api_hash || "";
  $("p-group").value = p.group_id || "";
  $("p-topic").value = p.topic_id || "0";
  $("p-conv").value = p.converted_dir || "";
  $("p-sess").value = p.session_dir || "";
}

$("b-pipe").addEventListener("click", () => API.pipeline_toggle());
$("stage-convert").addEventListener("click", () =>
  API.pipeline_stage(!$("stage-convert").classList.contains("on"), null));
$("stage-upload").addEventListener("click", () =>
  API.pipeline_stage(null, !$("stage-upload").classList.contains("on")));
$("b-pipesave").addEventListener("click", async () => {
  await API.pipeline_save({
    api_id: $("p-api-id").value, api_hash: $("p-api-hash").value,
    group_id: $("p-group").value, topic_id: $("p-topic").value,
    converted_dir: $("p-conv").value, session_dir: $("p-sess").value,
  });
  toast("Pipeline settings saved.");
});
$("b-reauth").addEventListener("click", async () => {
  const r = await API.pipeline_reauth(false);
  if (r.confirm) {
    modal("Re-auth", `Delete cached Telegram session in:\n${r.dir}\n\nYou'll need to log in again on next Start.`,
          "Delete session", async () => {
      const r2 = await API.pipeline_reauth(true);
      toast(r2.ok ? r2.msg : r2.error, !r2.ok);
    });
  } else {
    toast(r.msg || r.error, !r.ok);
  }
});

/* Telegram OTP / 2FA prompt (called from Python via evaluate_js). */
window.UI = {
  pipePrompt(label) {
    askText("Telegram", label, (v) => API.pipe_prompt_answer(v || ""), true,
            () => API.pipe_prompt_answer(""));
  },
  confirmQuit(n) {
    modal("Quit Scr33nX",
          `${n} recording(s) still active.\n\nQuit anyway? Recordings will be stopped.`,
          "Quit", () => API.quit());
  },
};

/* ══ wizard ══ */
const WIZ_STEPS = [
  { t: "Welcome", body: () => `
      <p>This wizard sets up the Telegram upload pipeline: it converts finished
      .ts recordings to .mp4 and uploads them to a group/topic of yours.</p>
      <p>You'll need Telegram API credentials from
      <b>https://my.telegram.org → API development tools</b>.</p>` },
  { t: "Telegram API credentials", body: () => `
      <div class="field"><label>API ID</label><input id="w-api-id" value="${esc($("p-api-id").value)}"></div>
      <div class="field"><label>API Hash</label><input id="w-api-hash" value="${esc($("p-api-hash").value)}"></div>` },
  { t: "Destination", body: () => `
      <div class="field"><label>Chat / Group ID (e.g. -1001234567890)</label>
        <input id="w-group" value="${esc($("p-group").value)}"></div>
      <div class="field"><label>Topic ID (0 if none)</label>
        <input id="w-topic" value="${esc($("p-topic").value || "0")}"></div>
      <div class="field"><label>Converted .mp4 folder (optional)</label>
        <input id="w-conv" value="${esc($("p-conv").value)}"></div>` },
  { t: "Review & finish", body: () => `
      <p>Settings will be saved to Pipeline/pipeline_settings.json.
      On the first Upload connect you'll be prompted for your phone number
      and login code.</p>
      <label class="toggle-row"><span class="cb on" id="w-startnow"></span>
        Start the pipeline with Upload enabled now</label>` },
];
let wizStep = 0;
const wizData = {};
$("b-wizard").addEventListener("click", () => { wizStep = 0; wizShow(); });
function wizShow() {
  const st = WIZ_STEPS[wizStep];
  $("wiz-title").textContent = `🧙 ${st.t}`;
  $("wiz-stepnum").textContent = `Step ${wizStep + 1} of ${WIZ_STEPS.length}`;
  $("wiz-body").innerHTML = st.body();
  $("wiz-back").style.visibility = wizStep ? "visible" : "hidden";
  $("wiz-next").textContent = wizStep === WIZ_STEPS.length - 1 ? "✓ Save & Finish" : "Next →";
  $("wizard").hidden = false;
}
function wizCollect() {
  for (const [id, k] of [["w-api-id", "api_id"], ["w-api-hash", "api_hash"],
                         ["w-group", "group_id"], ["w-topic", "topic_id"],
                         ["w-conv", "converted_dir"]]) {
    const el = $(id);
    if (el) wizData[k] = el.value;
  }
}
$("wiz-next").addEventListener("click", async () => {
  wizCollect();
  if (wizStep < WIZ_STEPS.length - 1) { wizStep++; wizShow(); return; }
  const startNow = $("w-startnow") && $("w-startnow").classList.contains("on");
  $("wizard").hidden = true;
  await API.pipeline_save(wizData);
  for (const [k, id] of [["api_id", "p-api-id"], ["api_hash", "p-api-hash"],
                         ["group_id", "p-group"], ["topic_id", "p-topic"],
                         ["converted_dir", "p-conv"]])
    if (wizData[k] !== undefined) $(id).value = wizData[k];
  if (startNow) {
    await API.pipeline_stage(null, true);
    if (!(S && S.pipe.running)) await API.pipeline_toggle();
    toast("Pipeline settings saved — starting with Upload enabled.");
  } else {
    toast("Pipeline settings saved.");
  }
});
$("wiz-back").addEventListener("click", () => { wizCollect(); wizStep--; wizShow(); });
$("wiz-cancel").addEventListener("click", () => { $("wizard").hidden = true; });

/* ══ settings ══ */
async function loadSettings() {
  if (settingsLoaded || !API) return;
  settingsLoaded = true;
  const s = await API.get_settings();
  $("s-output").value = s.output_dir;
  $("s-maxsize").value = s.max_size_mb;
  $("s-interval").value = s.check_interval;
  const q = $("s-quality");
  q.innerHTML = s.quality_options.map(o =>
    `<option value="${o.height}" ${o.height === s.max_quality ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  setSwitch("s-tray", s.minimize_to_tray);
  setSwitch("s-notif", s.notifications_enabled);
  setSwitch("s-gap", s.gap_warnings_enabled);
  setSwitch("s-n-started", s.notify_started);
  setSwitch("s-n-stopped", s.notify_stopped);
  setSwitch("s-n-down", s.notify_downgraded);
  setSwitch("s-n-disk", s.notify_lowdisk);
  setSwitch("s-vip-only", s.notify_vip_only);
  $("s-dur").value = s.notify_toast_secs;
  $("dur-val").textContent = s.notify_toast_secs;
  updateNotifDim();
  setSwitch("s-down", s.auto_downgrade_enabled);
  setSwitch("s-disk", s.low_disk_guard_enabled);
  $("s-disk-stop").value = s.low_disk_stop_gb;
  $("s-disk-resume").value = s.low_disk_resume_gb;
  updateDiskDim();
  setSwitch("s-pw", s.playwright_fallback_enabled);
  setSwitch("s-privacy", s.privacy_mode_enabled);
  const b = await API.browsers();
  const bs = $("s-browser");
  let opts = `<option value="">Ask each time</option><option value="system">System default</option>`;
  for (const br of b.browsers)
    opts += `<option value="${esc(br.path)}">${esc(br.name)}</option>`;
  bs.innerHTML = opts;
  bs.value = [...bs.options].some(o => o.value === s.preferred_browser)
    ? s.preferred_browser : "";
  $("s-pmode").value = s.preview_mode;
  $("s-pengine").value = s.preview_engine;
  $("s-ppath").value = s.preview_player_path;
  runSystemCheck();
  loadVip();
}
function setSwitch(id, on) { $(id).classList.toggle("on", !!on); }
function updateNotifDim() {
  $("notif-types").classList.toggle("disabled", !$("s-notif").classList.contains("on"));
}
function updateDiskDim() {
  const on = $("s-disk").classList.contains("on");
  $("disk-thresholds").classList.toggle("disabled", !on);
  $("s-disk-stop").disabled = !on;
  $("s-disk-resume").disabled = !on;
}
$("s-dur").addEventListener("input", (e) => { $("dur-val").textContent = e.target.value; });
$("s-notif").closest(".toggle-row").addEventListener("click", () => setTimeout(updateNotifDim, 0));
$("s-disk").closest(".toggle-row").addEventListener("click", () => setTimeout(updateDiskDim, 0));

async function loadVip() {
  if (!API) return;
  const r = await API.vip_get();
  $("vip-count").textContent = r.items.length;
  const box = $("vip-list");
  box.innerHTML = r.items.length
    ? r.items.map(it => `<span class="vip-chip">${esc(it.name)} <small>${esc(it.site)}</small>` +
        `<button data-vipdel="${esc(it.key)}" title="Remove from VIP">✕</button></span>`).join("")
    : `<span class="vip-empty">No VIP models yet.</span>`;
}
function vipRefresh(fn) { fn(); setTimeout(loadVip, 150); }
$("vip-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-vipdel]");
  if (!b || !API) return;
  await API.vip_remove([b.dataset.vipdel]);
  setTimeout(loadVip, 120);
});

$("s-pick").addEventListener("click", async () => {
  const r = await API.pick_folder($("s-output").value);
  if (r.path) $("s-output").value = r.path;
});
$("s-save").addEventListener("click", async () => {
  const r = await API.save_settings({
    output_dir: $("s-output").value,
    max_size_mb: $("s-maxsize").value,
    check_interval: $("s-interval").value,
    max_quality: $("s-quality").value,
    minimize_to_tray: $("s-tray").classList.contains("on"),
    notifications_enabled: $("s-notif").classList.contains("on"),
    gap_warnings_enabled: $("s-gap").classList.contains("on"),
    notify_started: $("s-n-started").classList.contains("on"),
    notify_stopped: $("s-n-stopped").classList.contains("on"),
    notify_downgraded: $("s-n-down").classList.contains("on"),
    notify_lowdisk: $("s-n-disk").classList.contains("on"),
    notify_toast_secs: $("s-dur").value,
    notify_vip_only: $("s-vip-only").classList.contains("on"),
    auto_downgrade_enabled: $("s-down").classList.contains("on"),
    low_disk_guard_enabled: $("s-disk").classList.contains("on"),
    low_disk_stop_gb: $("s-disk-stop").value,
    low_disk_resume_gb: $("s-disk-resume").value,
    playwright_fallback_enabled: $("s-pw").classList.contains("on"),
    privacy_mode_enabled: $("s-privacy").classList.contains("on"),
    preferred_browser: $("s-browser").value,
    preview_mode: $("s-pmode").value,
    preview_engine: $("s-pengine").value,
    preview_player_path: $("s-ppath").value,
  });
  toast("✓ Settings saved" + (r.note ? ` — ${r.note}` : ""));
});

async function runSystemCheck() {
  const r = await API.system_check();
  $("syscheck").innerHTML = r.rows.map(row => `
    <div class="sys-row">
      <span class="mark-${row.state}">${{ ok: "✓", warn: "⚠", err: "✕" }[row.state]}</span>
      ${esc(row.label)}<span class="path">${esc(row.detail)}</span>
      ${row.actions.map(a => `<button class="btn mini" data-act="${esc(a.act)}"
        data-arg="${esc(a.arg)}">${esc(a.label)}</button>`).join("")}
    </div>`).join("");
}
$("s-recheck").addEventListener("click", runSystemCheck);
$("syscheck").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  API.sys_fix(b.dataset.act, b.dataset.arg);
  toast("Started — check the Activity Log; then Re-check.");
});

/* ══ selection & interactions ══ */
function tabOf(el) { return el.closest('[data-panel="saved"]') ? "saved" : "rec"; }
function workset(tab) {
  return checked[tab].size ? [...checked[tab]] : [...sel[tab]];
}
function visibleOrder(tab) {
  return tab === "rec" ? recVisible
       : savedDisplay.filter(i => i.head === undefined).map(i => i.sid);
}

document.addEventListener("click", (e) => {
  if (suppressClick) { suppressClick = false; return; }
  closeCtx();

  // Settings switches, wizard/browser-picker checkboxes: a plain visual
  // toggle read on Save (they carry no data-* hook of their own).
  const trow = e.target.closest("label.toggle-row");
  if (trow) {
    const t = trow.querySelector(".switch, .cb");
    if (t) t.classList.toggle("on");
    return;
  }

  const tog = e.target.closest("[data-tog]");
  if (tog) {
    const [tab, site] = tog.dataset.tog.split(":");
    if (collapsed[tab].has(site)) collapsed[tab].delete(site);
    else collapsed[tab].add(site);
    if (tab === "rec") { $("rows").dataset.sig = ""; renderRec(S.models); }
    else rebuildSavedDisplay();
    return;
  }

  const cb = e.target.closest(".cb[data-cb]");
  if (cb) {
    const tab = tabOf(cb);
    const key = cb.dataset.cb;
    if (checked[tab].has(key)) checked[tab].delete(key);
    else checked[tab].add(key);
    cb.classList.toggle("on", checked[tab].has(key));
    updateSelLabel();
    return;
  }

  const sw = e.target.closest(".switch[data-auto]");
  if (sw && API) {
    const on = !sw.classList.contains("on");
    sw.classList.toggle("on", on);
    API.set_auto(sw.dataset.auto, on);
    return;
  }

  const star = e.target.closest(".stars[data-stars] i");
  if (star && API) {
    const wrap = star.parentElement;
    const key = wrap.dataset.stars;
    const cur = parseInt(wrap.dataset.rank, 10) || 0;
    const idx = parseInt(star.dataset.i, 10);
    const target = idx === cur ? 0 : idx;
    const saved = key.startsWith("saved:");
    const doIt = () => API.set_rank([key], target, saved);
    if (cur !== 0) {
      const stars = (n) => "★".repeat(n) + "☆".repeat(5 - n);
      modal("Change rank",
            `${target === 0 ? "Clear" : "Change"} the rank?\n\n${stars(cur)}  →  ${stars(target)}`,
            "Yes", doIt);
    } else doIt();
    return;
  }

  const row = e.target.closest(".row[data-key]");
  if (row) {
    const tab = row.dataset.tab === "saved" ? "saved" : "rec";
    const key = row.dataset.key;
    const order = visibleOrder(tab);
    if (e.shiftKey && anchor[tab]) {
      const a = order.indexOf(anchor[tab]), b = order.indexOf(key);
      if (a >= 0 && b >= 0) {
        sel[tab].clear();
        for (let i = Math.min(a, b); i <= Math.max(a, b); i++) sel[tab].add(order[i]);
      }
    } else if (e.ctrlKey) {
      if (sel[tab].has(key)) sel[tab].delete(key); else sel[tab].add(key);
      anchor[tab] = key;
    } else {
      sel[tab].clear();
      sel[tab].add(key);
      anchor[tab] = key;
    }
    refreshSelectionClasses(tab);
    return;
  }

  // Click on empty table space clears the current selection.
  const gw = e.target.closest(".grid-wrap");
  if (gw) {
    const tab = gw.closest('[data-panel="saved"]') ? "saved" : "rec";
    if (sel[tab].size) { sel[tab].clear(); anchor[tab] = null; refreshSelectionClasses(tab); }
  }
});

function refreshSelectionClasses(tab) {
  if (tab === "rec") {
    for (const [key, el] of rowEls) el.classList.toggle("selected", sel.rec.has(key));
    updateSelLabel();
  } else {
    renderSavedWindow();
  }
}

/* Ctrl/Cmd+A — select every visible row in the active table tab (respects the
   current filter). Ignored while typing in a field so it still selects text. */
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || (e.key !== "a" && e.key !== "A")) return;
  const t = e.target;
  if (t instanceof Element && t.matches("input, textarea, select")) return;
  const panel = document.querySelector(".panel.active");
  const p = panel && panel.dataset.panel;
  const tab = p === "recorder" ? "rec" : p === "saved" ? "saved" : null;
  if (!tab) return;
  e.preventDefault();
  const order = visibleOrder(tab);
  sel[tab].clear();
  for (const k of order) sel[tab].add(k);
  if (order.length) anchor[tab] = order[order.length - 1];
  refreshSelectionClasses(tab);
});

/* marquee drag-selection */
let mq = null, mqStart = null;
document.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const wrap = e.target.closest(".grid-wrap");
  if (!wrap || !wrap.closest('[data-panel="recorder"], [data-panel="saved"]')) return;
  if (e.target.closest(".cb, .switch, .stars, .tog, button, input, select")) return;
  mqStart = { x: e.clientX, y: e.clientY, wrap, tab: tabOf(wrap) };
});
document.addEventListener("mousemove", (e) => {
  if (!mqStart) return;
  if (!mq) {
    if (Math.abs(e.clientX - mqStart.x) < 5 && Math.abs(e.clientY - mqStart.y) < 5) return;
    mq = document.createElement("div");
    mq.className = "marquee";
    document.body.appendChild(mq);
    sel[mqStart.tab].clear();
  }
  const x1 = Math.min(mqStart.x, e.clientX), y1 = Math.min(mqStart.y, e.clientY);
  const x2 = Math.max(mqStart.x, e.clientX), y2 = Math.max(mqStart.y, e.clientY);
  Object.assign(mq.style, { left: x1 + "px", top: y1 + "px",
                            width: (x2 - x1) + "px", height: (y2 - y1) + "px" });
  const tab = mqStart.tab;
  mqStart.wrap.querySelectorAll(".row").forEach(r => {
    const b = r.getBoundingClientRect();
    const hit = !(b.right < x1 || b.left > x2 || b.bottom < y1 || b.top > y2);
    if (hit) sel[tab].add(r.dataset.key); else sel[tab].delete(r.dataset.key);
    r.classList.toggle("selected", hit);
  });
  if (tab === "rec") updateSelLabel();
  e.preventDefault();
});
document.addEventListener("mouseup", () => {
  if (mq) { mq.remove(); suppressClick = true; }
  mq = null; mqStart = null;
});

/* ══ context menus ══ */
function closeCtx() { const c = $("ctx"); c.hidden = true; c.innerHTML = ""; }

const menuActions = new Map();
let actSeq = 0;
function showMenu(items, x, y) {
  menuActions.clear();
  actSeq = 0;
  const withIds = (list) => list.map(it => {
    if (it === "hr" || !it) return it;
    const copy = { ...it };
    if (copy.sub) copy.sub = withIds(copy.sub);
    else if (copy.action) { copy.id = String(++actSeq); menuActions.set(copy.id, copy.action); }
    return copy;
  });
  const tagged = withIds(items.filter(Boolean));
  const html = (function build(list) {
    let h = "";
    for (const it of list) {
      if (it === "hr") { h += "<hr>"; continue; }
      const cls = `mi ${it.danger ? "danger" : ""} ${it.disabled ? "disabled" : ""}`;
      if (it.sub) h += `<div class="${cls}">${esc(it.label)}<span class="sub-mark">▸</span><div class="ctx sub">${build(it.sub)}</div></div>`;
      else h += `<div class="${cls}" data-id="${it.id}">${esc(it.label)}</div>`;
    }
    return h;
  })(tagged);
  const c = $("ctx");
  c.innerHTML = html;
  c.hidden = false;
  const mw = c.offsetWidth, mh = c.offsetHeight;
  c.style.left = Math.min(x, innerWidth - mw - 8) + "px";
  c.style.top = Math.min(y, innerHeight - mh - 8) + "px";
}
$("ctx").addEventListener("click", (e) => {
  const mi = e.target.closest(".mi[data-id]");
  if (!mi) return;
  const fn = menuActions.get(mi.dataset.id);
  closeCtx();
  if (fn) fn();
  e.stopPropagation();
});
document.addEventListener("mousedown", (e) => {
  if (!$("ctx").hidden && !e.target.closest("#ctx")) closeCtx();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeCtx();
    $("modal").hidden = true;
    $("prompt").hidden = true;
    $("bpick").hidden = true;
    $("addsaved").hidden = true;
  }
});
window.addEventListener("blur", closeCtx);

function rankSub(keys, saved) {
  const sub = [{ label: "☆  Clear rank", action: () => API.set_rank(keys, 0, saved) }];
  for (let r = 1; r <= 5; r++)
    sub.push({ label: "★".repeat(r) + "☆".repeat(5 - r),
               action: () => API.set_rank(keys, r, saved) });
  return sub;
}
function qualitySub(keys, cur) {
  const sub = [{ label: `${cur === 0 ? "●  " : "    "}Default (use global setting)`,
                 action: () => API.set_quality(keys, 0) }];
  for (const o of qualityOpts) {
    if (!o.height) continue;
    sub.push({ label: `${cur === o.height ? "●  " : "    "}${o.label}`,
               action: () => API.set_quality(keys, o.height) });
  }
  return sub;
}
function checkHelpers(tab) {
  return [
    "hr",
    sel[tab].size ? { label: `☑  Check Selected  (${sel[tab].size})`,
      action: () => { for (const k of sel[tab]) checked[tab].add(k); retab(tab); } } : null,
    { label: "☑  Check All Visible",
      action: () => { for (const k of visibleOrder(tab)) checked[tab].add(k); retab(tab); } },
    checked[tab].size ? { label: `☐  Uncheck All  (${checked[tab].size})`,
      action: () => { checked[tab].clear(); retab(tab); } } : null,
  ];
}
function retab(tab) {
  if (tab === "rec") { $("rows").dataset.sig = ""; renderRec(S.models); }
  else renderSavedWindow();
  updateSelLabel();
}

document.addEventListener("contextmenu", (e) => {
  const row = e.target.closest(".row[data-key]");
  if (!row) return;
  e.preventDefault();
  const tab = row.dataset.tab === "saved" ? "saved" : "rec";
  const key = row.dataset.key;
  if (!sel[tab].has(key) && !checked[tab].has(key)) {
    sel[tab].clear();
    sel[tab].add(key);
    anchor[tab] = key;
    refreshSelectionClasses(tab);
  }
  const keys = workset(tab).length ? workset(tab) : [key];
  const n = keys.length;
  const single = n === 1;
  const k0 = keys[0];

  if (tab === "rec") {
    const m = (S.models || []).find(x => x.key === k0);
    const items = single ? [
      { label: `▶  Start Recording  ${m.name}`, action: () => API.record(keys, true) },
      { label: "⏹  Stop Recording", action: () => API.record(keys, false) },
      { label: `${m.auto ? "☑" : "☐"}  Auto-Record`, action: () => API.toggle_auto(keys) },
      { label: `🎞  Max Quality  (${m.q ? m.q + "p" : "Default"})`, sub: qualitySub(keys, m.q) },
      "hr",
      m.saved
        ? { label: "✕  Remove from Saved Models",
            action: () => API.saved_remove([`saved:${k0}`]) }
        : { label: "⭐  Add to Saved Models", action: () => API.add_saved(keys) },
      { label: "▶  Preview", action: () => doPreview(m.name, m.site) },
      { label: "⭐  Set Rank", sub: rankSub(keys, false) },
      m.vip ? { label: "🌟  Remove from VIP List", action: () => vipRefresh(() => API.vip_remove(keys)) }
            : { label: "🌟  Add to VIP List", action: () => vipRefresh(() => API.vip_add(keys)) },
      "hr",
      { label: "🔗  Copy Model URL", action: () => API.copy_urls(keys, false, false) },
      { label: "🌐  Open in Browser", action: () => openBrowser(keys, false, false) },
      { label: "🌐  Open in Browser (choose…)", action: () => openBrowser(keys, false, true) },
      { label: "📁  Open Output Folder", action: () => API.open_output_folder() },
      "hr",
      { label: "✕  Remove Model", danger: true,
        action: () => API.remove(keys) },
      ...checkHelpers(tab),
    ] : [
      { label: `▶  Start Recording  (${n} selected)`, action: () => API.record(keys, true) },
      { label: `⏹  Stop Recording  (${n} selected)`, action: () => API.record(keys, false) },
      { label: `☑  Toggle AUTO  (${n} selected)`, action: () => API.toggle_auto(keys) },
      { label: `🎞  Max Quality  (${n} selected)`, sub: qualitySub(keys, null) },
      { label: `⭐  Set Rank  (${n} selected)`, sub: rankSub(keys, false) },
      { label: `🌟  Add to VIP List  (${n})`, action: () => vipRefresh(() => API.vip_add(keys)) },
      { label: `🌟  Remove from VIP List  (${n})`, action: () => vipRefresh(() => API.vip_remove(keys)) },
      "hr",
      { label: `🌐  Open in Browser  (${n})`, action: () => openBrowser(keys, false, false) },
      { label: `🌐  Open in Browser (choose…)  (${n})`, action: () => openBrowser(keys, false, true) },
      { label: `📋  Copy as OneTab List  (${n})`, action: () => API.copy_urls(keys, false, true) },
      { label: "📁  Open Output Folder", action: () => API.open_output_folder() },
      "hr",
      { label: `✕  Remove  (${n})`, danger: true,
        action: () => modal("Remove models", `Remove ${n} model(s) from the Recorder?`,
                            "Remove", () => API.remove(keys)) },
      ...checkHelpers(tab),
    ];
    showMenu(items, e.clientX, e.clientY);
  } else {
    const it = savedCache.items.find(x => x.sid === k0) || { name: "?", site: "?" };
    const st0 = (S.saved_status || {})[k0];
    const liveNow = st0 && (st0[0] === "online" || st0[0] === "recording");
    const items = single ? [
      { label: `＋  Add to Recorder  ${it.name}`, action: () => API.saved_to_recorder(keys) },
      liveNow ? { label: "▶  Add to Recorder & Start Recording",
                  action: () => API.saved_record(keys) } : null,
      { label: "▶  Preview", action: () => doPreview(it.name, it.site) },
      "hr",
      { label: "🔗  Copy Model URL", action: () => API.copy_urls(keys, true, false) },
      { label: "🌐  Open in Browser", action: () => openBrowser(keys, true, false) },
      { label: "🌐  Open in Browser (choose…)", action: () => openBrowser(keys, true, true) },
      { label: "⭐  Set Rank", sub: rankSub(keys, true) },
      it.vip ? { label: "🌟  Remove from VIP List", action: () => vipRefresh(() => API.vip_remove(keys)) }
             : { label: "🌟  Add to VIP List", action: () => vipRefresh(() => API.vip_add(keys)) },
      "hr",
      { label: "✕  Remove from Saved Models", danger: true,
        action: () => API.saved_remove(keys) },
      ...checkHelpers(tab),
    ] : [
      { label: `＋  Add to Recorder  (${n})`, action: () => API.saved_to_recorder(keys) },
      { label: `⭐  Set Rank  (${n})`, sub: rankSub(keys, true) },
      { label: `🌟  Add to VIP List  (${n})`, action: () => vipRefresh(() => API.vip_add(keys)) },
      { label: `🌟  Remove from VIP List  (${n})`, action: () => vipRefresh(() => API.vip_remove(keys)) },
      "hr",
      { label: `🌐  Open in Browser  (${n})`, action: () => openBrowser(keys, true, false) },
      { label: `🌐  Open in Browser (choose…)  (${n})`, action: () => openBrowser(keys, true, true) },
      { label: `📋  Copy as OneTab List  (${n})`, action: () => API.copy_urls(keys, true, true) },
      "hr",
      { label: `✕  Remove from Saved  (${n})`, danger: true,
        action: () => modal("Remove from Saved", `Remove ${n} model(s) from Saved Models?`,
                            "Remove", () => API.saved_remove(keys)) },
      ...checkHelpers(tab),
    ];
    showMenu(items, e.clientX, e.clientY);
  }
});

/* ══ browser picker ══ */
async function openBrowser(keys, saved, forceChoose) {
  if (keys.length > 10) {
    modal("Open in Browser", `Open ${keys.length} tabs in your browser?`, "Open",
          () => openBrowser2(keys, saved, forceChoose));
    return;
  }
  openBrowser2(keys, saved, forceChoose);
}
async function openBrowser2(keys, saved, forceChoose) {
  if (!forceChoose) {
    const r = await API.open_browser(keys, saved);
    if (!r.choose) return;
  }
  const b = await API.browsers();
  let list = `<label class="toggle-row"><input type="radio" name="bp" value="system" checked> System default</label>`;
  for (const br of b.browsers)
    list += `<label class="toggle-row"><input type="radio" name="bp" value="${esc(br.path)}"> ${esc(br.name)}</label>`;
  $("bpick-list").innerHTML = list;
  $("bpick-remember").classList.remove("on");
  $("bpick").hidden = false;
  $("bpick-ok").onclick = () => {
    const target = document.querySelector('input[name="bp"]:checked').value;
    const remember = $("bpick-remember").classList.contains("on");
    $("bpick").hidden = true;
    API.open_browser(keys, saved, target, remember && !forceChoose);
  };
  $("bpick-cancel").onclick = () => { $("bpick").hidden = true; };
}
// bpick-remember toggles via the shared label.toggle-row handler above.

/* ══ preview ══ */
let hls = null;
async function doPreview(name, site) {
  toast(`● Opening preview for ${name} (${site}) — resolving the stream…`);
  const r = await API.preview(name, site);
  if (!r.ok) { toast(r.error || "Preview failed.", true); return; }
  if (r.mode === "external") { toast(`Preview: ${r.player} window opened.`); return; }
  const video = $("player-video");
  $("player-title").textContent = `▶ ${r.title}`;
  $("player").hidden = false;
  if (window.Hls && Hls.isSupported()) {
    if (hls) hls.destroy();
    hls = new Hls({ liveDurationInfinity: true });
    hls.loadSource(r.url);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) { toast("Preview playback error.", true); closePlayer(); }
    });
  } else {
    video.src = r.url;   // native HLS fallback (not on Windows, but harmless)
  }
}
function closePlayer() {
  if (hls) { hls.destroy(); hls = null; }
  const v = $("player-video");
  v.pause();
  v.removeAttribute("src");
  v.load();
  $("player").hidden = true;
}
$("player-close").addEventListener("click", closePlayer);

/* ══ toolbar wiring ══ */
function needSel(tab) {
  const keys = workset(tab);
  if (!keys.length) { toast("Select or check model(s) first.", true); return null; }
  return keys;
}
$("b-rec").addEventListener("click", () => { const k = needSel("rec"); if (k) API.record(k, true); });
$("b-stop").addEventListener("click", () => { const k = needSel("rec"); if (k) API.record(k, false); });
$("b-auto").addEventListener("click", () => { const k = needSel("rec"); if (k) API.toggle_auto(k); });
$("b-remove").addEventListener("click", () => {
  const k = needSel("rec");
  if (k) modal("Remove models", `Remove ${k.length} model(s) from the Recorder?\n(Recording models are skipped.)`,
               "Remove", () => { API.remove(k); checked.rec.clear(); sel.rec.clear(); });
});
$("b-remoff").addEventListener("click", async () => {
  const r = await API.offline_count();
  if (!r.count) { toast("No OFFLINE models to remove."); return; }
  modal("Remove Offline", `Remove every model currently OFFLINE (${r.count})?\nRecording/private/checking rows and Saved Models are untouched.`,
        "Remove", () => API.remove_offline());
});
$("b-addsaved").addEventListener("click", () => { const k = needSel("rec"); if (k) API.add_saved(k); });
$("b-clear").addEventListener("click", () =>
  modal("Clear recorder", "Stop everything and remove ALL models from the Recorder?\n\nThis stops the monitor, all active downloads, clears AUTO, and empties the Recorder list. Saved Models are kept.",
        "Clear", () => { API.clear_recorder(); checked.rec.clear(); sel.rec.clear(); }));
$("b-stopall").addEventListener("click", () =>
  modal("Stop all downloads", "Force-stop ALL active downloads and uncheck AUTO on every model?",
        "Stop all", () => API.stop_all()));
$("btn-monitor").addEventListener("click", async () => {
  await API.set_monitor("recorder", $("btn-monitor").classList.contains("go"));
  tick();
});
$("btn-scanner").addEventListener("click", async () => {
  await API.set_monitor("saved", $("btn-scanner").classList.contains("go"));
  tick();
});

$("b-export").addEventListener("click", async () => {
  const r = await API.saved_export();
  if (r.cancelled) return;
  if (r.ok) modal("Export complete", r.msg, "OK", () => {}, true);
  else toast(r.error, true);
});
$("b-import").addEventListener("click", async () => {
  const r = await API.saved_import();
  if (r.cancelled) return;
  if (r.ok) { modal("Import complete", r.msg, "OK", () => {}, true); savedCache.version = -1; }
  else toast(r.error, true);
});
$("b-savedadd").addEventListener("click", () => {
  $("addsaved-input").value = "";
  $("addsaved").hidden = false;
  setTimeout(() => $("addsaved-input").focus(), 60);
});
$("addsaved-cancel").addEventListener("click", () => { $("addsaved").hidden = true; });
$("addsaved-ok").addEventListener("click", doAddSaved);
$("addsaved-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doAddSaved(); });
async function doAddSaved() {
  const raw = $("addsaved-input").value.trim();
  if (!raw) { toast("Enter a username or link.", true); return; }
  $("addsaved").hidden = true;
  const r = await API.saved_add(raw, $("addsaved-site").value);
  toast(r.ok ? `⭐ Added ${r.name} (${r.site}) to Saved Models` : r.error, !r.ok);
}

/* filters + sorting */
$("rec-filter").addEventListener("input", (e) => { filter.rec = e.target.value.trim(); retab("rec"); });
$("saved-filter").addEventListener("input", (e) => { filter.saved = e.target.value.trim(); rebuildSavedDisplay(); });

/* Multi-select status filters (pick any combination, e.g. Online + Recording). */
const CAP = (s) => s.charAt(0).toUpperCase() + s.slice(1);
function updateMsfLabel(tab) {
  const set = statusFilter[tab];
  const root = $(`${tab}-msf`);
  const btn = root.querySelector(".msf-btn");
  btn.classList.toggle("active", set.size > 0);
  btn.textContent = (set.size === 0 ? "Status: All"
    : set.size === 1 ? "Status: " + CAP([...set][0])
    : `Status: ${set.size}`) + " ▾";
  root.querySelectorAll(".msf-menu label[data-v]").forEach(l => {
    const cb = l.querySelector(".cb");
    if (cb) cb.classList.toggle("on", set.has(l.dataset.v));
  });
}
for (const tab of ["rec", "saved"]) {
  const root = $(`${tab}-msf`);
  const menu = root.querySelector(".msf-menu");
  root.querySelector(".msf-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    const wasOpen = !menu.hidden;
    document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
    menu.hidden = wasOpen;
  });
  menu.addEventListener("click", (e) => {
    e.stopPropagation();
    const lab = e.target.closest("label[data-v]");
    if (!lab) return;
    const v = lab.dataset.v;
    if (v === "__clear") statusFilter[tab].clear();
    else if (statusFilter[tab].has(v)) statusFilter[tab].delete(v);
    else statusFilter[tab].add(v);
    updateMsfLabel(tab);
    if (tab === "rec") retab("rec"); else rebuildSavedDisplay();
  });
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".msf"))
    document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
});
for (const [headId, tab] of [["rec-head", "rec"], ["saved-head", "saved"]]) {
  $(headId).addEventListener("click", (e) => {
    const col = e.target.closest(".sortable");
    if (!col) return;
    const c = col.dataset.col;
    if (sort[tab].col === c) sort[tab].dir = -sort[tab].dir;
    else sort[tab] = { col: c, dir: 1 };
    if (tab === "rec") retab("rec"); else rebuildSavedDisplay();
  });
}

/* add model / header buttons */
$("btn-add").addEventListener("click", addModel);
$("add-name").addEventListener("keydown", (e) => { if (e.key === "Enter") addModel(); });
async function addModel() {
  if (!API) return;
  const raw = $("add-name").value.trim();
  if (!raw) { toast("Enter a model username or link.", true); return; }
  const res = await API.add_model(raw, $("add-site").value);
  if (res.ok) { $("add-name").value = ""; toast(`Added ${res.name} (${res.site})`); tick(); }
  else toast(res.error || "Could not add model.", true);
}
$("btn-term").addEventListener("click", () => {
  const n = (S && S.active_recordings) || 0;
  if (n > 0)
    modal("Terminate Scr33nX",
          `${n} recording(s) still active.\n\nForce-terminate now? Their final segments will be dropped.`,
          "Terminate", () => API.terminate());
  else API.terminate();
});
$("btn-clearlog").addEventListener("click", () => { $("logpane").innerHTML = ""; });
$("update-pill").addEventListener("click", () =>
  API.open_url("https://github.com/00Sylar/Scr33nX/releases/latest"));

/* tabs */
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
  document.querySelector(".tab.active").classList.remove("active");
  t.classList.add("active");
  document.querySelector(".panel.active").classList.remove("active");
  document.querySelector(`.panel[data-panel="${t.dataset.tab}"]`).classList.add("active");
  if (t.dataset.tab === "settings") { loadSettings(); loadVip(); }
  if (t.dataset.tab === "output") loadPipeFields();
  if (t.dataset.tab === "saved") { savedCache.version = -1; renderSaved(); }
}));

/* ══ privacy mode (idle starfield cover) ══ */
let privacyOn = false, covered = false, lastActivity = Date.now(), starsRAF = 0;
function privacyApply(on) {
  privacyOn = !!on;
  if (!privacyOn && covered) uncover();
}
["mousemove", "keydown", "mousedown", "wheel"].forEach(ev =>
  document.addEventListener(ev, () => {
    lastActivity = Date.now();
  }, { passive: true }));
setInterval(() => {
  if (privacyOn && !covered && Date.now() - lastActivity > 3000) cover();
}, 500);
function cover() {
  covered = true;
  $("privacy").hidden = false;
  $("privacy-exit").hidden = true;
  startStars();
}
function uncover() {
  covered = false;
  $("privacy").hidden = true;
  cancelAnimationFrame(starsRAF);
  lastActivity = Date.now();
}
$("privacy").addEventListener("click", (e) => {
  if (e.target.closest(".privacy-exit")) return;
  $("privacy-exit").hidden = false;
});
$("privacy-stay").addEventListener("click", () => { $("privacy-exit").hidden = true; });
$("privacy-leave").addEventListener("click", uncover);
function startStars() {
  const cv = $("stars");
  const ctx2 = cv.getContext("2d");
  cv.width = innerWidth;
  cv.height = innerHeight;
  const stars = Array.from({ length: 130 }, () => ({
    x: Math.random() * cv.width, y: Math.random() * cv.height,
    r: Math.random() * 1.4 + 0.3, s: Math.random() * 0.25 + 0.05,
    tw: Math.random() * Math.PI * 2,
  }));
  (function frame() {
    if (!covered) return;
    ctx2.fillStyle = "#03030a";
    ctx2.fillRect(0, 0, cv.width, cv.height);
    for (const st of stars) {
      st.y += st.s;
      st.tw += 0.03;
      if (st.y > cv.height) { st.y = -2; st.x = Math.random() * cv.width; }
      ctx2.globalAlpha = 0.5 + 0.5 * Math.sin(st.tw);
      ctx2.fillStyle = "#cfd2e0";
      ctx2.beginPath();
      ctx2.arc(st.x, st.y, st.r, 0, 7);
      ctx2.fill();
    }
    ctx2.globalAlpha = 1;
    starsRAF = requestAnimationFrame(frame);
  })();
}

/* ══ modal / prompt / toast ══ */
function modal(title, text, okLabel, onOk, infoOnly = false) {
  $("modal-title").textContent = title;
  $("modal-text").textContent = text;
  $("modal-ok").textContent = okLabel;
  $("modal-cancel").style.display = infoOnly ? "none" : "";
  $("modal").hidden = false;
  $("modal-ok").onclick = () => { $("modal").hidden = true; onOk(); };
  $("modal-cancel").onclick = () => { $("modal").hidden = true; };
}
function askText(title, text, onOk, password = false, onCancel = null) {
  $("prompt-title").textContent = title;
  $("prompt-text").textContent = text;
  const inp = $("prompt-input");
  inp.type = password ? "password" : "text";
  inp.value = "";
  $("prompt").hidden = false;
  setTimeout(() => inp.focus(), 60);
  const finish = (v) => { $("prompt").hidden = true; if (v === null) { if (onCancel) onCancel(); } else onOk(v); };
  $("prompt-ok").onclick = () => finish(inp.value.trim());
  $("prompt-cancel").onclick = () => finish(null);
  inp.onkeydown = (e) => { if (e.key === "Enter") finish(inp.value.trim()); };
}
let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3600);
}
