const API = 'http://localhost:5200';  // v2 (TEST) port

// ── Live-update state ──────────────────────────────────────────────────────────
// The popup keeps polling the backend while it is open, so the UI reflects status
// changes (e.g. a Start that takes a few seconds to fetch the URL + spawn ffmpeg)
// without the user having to close and reopen it. We only re-render when the
// meaningful state actually changes, so buttons and feedback don't flicker on
// every poll.
const POLL_MS = 1200;
let pageInfo    = null;   // { name, site } for the active tab (resolved once on open)
let lastSig     = null;   // signature of the last rendered backend state
let resyncTimer = null;   // fallback re-render after a Start/Stop action

function stateSig(appData) {
  return appData
    ? `${appData.in_recorder}|${appData.in_saved}|${appData.status}|${appData.auto}`
    : 'down';
}

function scheduleResync(ms) {
  clearTimeout(resyncTimer);
  resyncTimer = setTimeout(() => render(), ms);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractModelInfo() {
  // Runs inside the page context via scripting.executeScript
  const host = window.location.hostname.replace('www.', '');
  const path = window.location.pathname;
  let site = null;
  if (host.includes('chaturbate.com')) site = 'chaturbate';
  else if (host.includes('stripchat.com')) site = 'stripchat';
  else if (host.includes('camsoda.com'))    site = 'camsoda';
  else if (host.includes('myfreecams.com')) site = 'myfreecams';
  if (!site) return null;
  let name;
  if (site === 'myfreecams') {
    // SPA: the model lives in the hash fragment (#name, #/name, #/model/<id>),
    // with a /models/<name> path fallback.
    let h = window.location.hash.replace(/^#\/?/, '');
    if (h.startsWith('model/')) h = h.slice('model/'.length);
    name = h.split(/[/?]/).filter(Boolean)[0] || '';
    if (!name) name = path.split('/').filter(Boolean).pop() || '';
  } else {
    name = path.split('/').filter(Boolean)[0] || '';
  }
  const skip  = ['tags','search','following','discover','login','register',
                 'promo','affiliates','p','trending','new-cams','female','male'];
  if (!name || skip.includes(name.toLowerCase())) return null;
  return { name: name.toLowerCase(), site };
}

async function getPageModel() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractModelInfo,
    });
    return result;
  } catch {
    return null;
  }
}

async function fetchStatus(name, site) {
  try {
    const r = await fetch(
      `${API}/status?name=${encodeURIComponent(name)}&site=${encodeURIComponent(site)}`,
      { signal: AbortSignal.timeout(1500) }
    );
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

async function postAdd(name, site, target) {
  const r = await fetch(`${API}/add`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, site, target }),
    signal:  AbortSignal.timeout(3000),
  });
  return r.json();
}

async function postRecord(name, site, action) {
  const r = await fetch(`${API}/record`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, site, action }),
    signal:  AbortSignal.timeout(3000),
  });
  return r.json();
}

async function postRemove(name, site, target) {
  const r = await fetch(`${API}/remove`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, site, target }),
    signal:  AbortSignal.timeout(3000),
  });
  return r.json();
}

async function postAuto(name, site, enabled) {
  const r = await fetch(`${API}/auto`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, site, enabled }),
    signal:  AbortSignal.timeout(3000),
  });
  return r.json();
}

// ── Status badge ──────────────────────────────────────────────────────────────

function statusBadgeHTML(s) {
  if (!s) return '';
  const labels = {
    recording: '⬤  RECORDING',
    online:    '●  ONLINE',
    offline:   '●  OFFLINE',
    checking:  '◌  CHECKING…',
    error:     '⚠  ERROR',
  };
  return `<div class="status-badge ${s}">${labels[s] || s.toUpperCase()}</div>`;
}

// ── Render ────────────────────────────────────────────────────────────────────

function recorderButtonHTML(inRec, status, appUp) {
  if (!inRec) {
    return `<button class="btn btn-rec" id="btn-rec" ${!appUp ? 'disabled' : ''}>
      ＋  Add to Recorder
    </button>`;
  }
  // Already in Recorder — render Start or Stop based on status
  if (status === 'recording') {
    return `<button class="btn btn-stop" id="btn-stop">⏹  Stop Recording</button>`;
  }
  if (status === 'checking') {
    return `<button class="btn btn-ghost" id="btn-checking" disabled>
      ◌  Checking…
    </button>`;
  }
  // online / offline / error / null → allow manual start
  return `<button class="btn btn-start" id="btn-start">⏺  Start Recording</button>`;
}

async function render(appData) {
  const root = document.getElementById('root');

  const info = pageInfo;
  if (!info) {
    root.innerHTML = `<div class="msg">
      <span class="icon">🔍</span>
      No model detected<br>on this page.
    </div>`;
    return;
  }

  // Called from a poll with fresh data, or standalone (undefined) → fetch it.
  if (appData === undefined) appData = await fetchStatus(info.name, info.site);
  lastSig = stateSig(appData);

  const appUp     = appData !== null;
  const inRec     = appData?.in_recorder ?? false;
  const inSaved   = appData?.in_saved    ?? false;
  const curStatus = appData?.status      ?? null;

  const siteLabel = info.site.charAt(0).toUpperCase() + info.site.slice(1);

  // "Remove from Recorder" is only offered when in the list AND idle
  // (not recording, not mid-check). Protects against removing an active session.
  const canRemoveRec = inRec && appUp
    && curStatus !== 'recording'
    && curStatus !== 'checking';

  root.innerHTML = `
    <div class="model-name">${info.name}</div>
    <div class="site-label">${siteLabel}</div>
    ${statusBadgeHTML(curStatus)}
    ${recorderButtonHTML(inRec, curStatus, appUp)}
    ${inRec ? `<label class="auto-row">
        <input type="checkbox" id="chk-auto" ${appData?.auto ? 'checked' : ''}>
        <span>Auto-Record when online</span>
      </label>` : ''}
    <button class="btn btn-saved"
      id="btn-saved"
      ${inSaved || !appUp ? 'disabled' : ''}>
      ${inSaved ? '⭐  In Saved Models' : '⭐  Add to Saved Models'}
    </button>
    ${canRemoveRec
      ? '<button class="btn btn-remove" id="btn-remove-rec">✕  Remove from Recorder</button>'
      : ''}
    ${!appUp ? '<div class="feedback err">StreamRecorder is not running</div>' : ''}
    <div id="fb"></div>
  `;

  if (!appUp) return;

  // ── Add to Recorder ─────────────────────────────────────────────────────────
  const btnRec = document.getElementById('btn-rec');
  if (btnRec) {
    btnRec.addEventListener('click', async () => {
      const fb  = document.getElementById('fb');
      btnRec.disabled = true;
      btnRec.textContent = '…';
      try {
        const res = await postAdd(info.name, info.site, 'recorder');
        if (res.ok) {
          // Re-render so the button flips to Start/Stop based on live status
          setTimeout(render, 250);
        } else {
          btnRec.disabled = false;
          btnRec.textContent = '＋  Add to Recorder';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnRec.disabled = false;
        btnRec.textContent = '＋  Add to Recorder';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Start Recording ────────────────────────────────────────────────────────
  const btnStart = document.getElementById('btn-start');
  if (btnStart) {
    btnStart.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnStart.disabled = true;
      btnStart.textContent = '…  Starting';
      try {
        const res = await postRecord(info.name, info.site, 'start');
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = 'Starting… (updates automatically)';
          // The button stays in this "…  Starting" state; the live poll flips it
          // to Stop as soon as the backend reports RECORDING. A fallback resync
          // recovers the UI if the status never changes (e.g. the model is offline).
          scheduleResync(10000);
        } else {
          btnStart.disabled = false;
          btnStart.textContent = '⏺  Start Recording';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnStart.disabled = false;
        btnStart.textContent = '⏺  Start Recording';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Stop Recording ─────────────────────────────────────────────────────────
  const btnStop = document.getElementById('btn-stop');
  if (btnStop) {
    btnStop.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnStop.disabled = true;
      btnStop.textContent = '…  Stopping';
      try {
        const res = await postRecord(info.name, info.site, 'stop');
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = 'Stopping… (updates automatically)';
          scheduleResync(6000);
        } else {
          btnStop.disabled = false;
          btnStop.textContent = '⏹  Stop Recording';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnStop.disabled = false;
        btnStop.textContent = '⏹  Stop Recording';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Remove from Recorder (only when in list and not recording) ─────────────
  const btnRemoveRec = document.getElementById('btn-remove-rec');
  if (btnRemoveRec) {
    btnRemoveRec.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnRemoveRec.disabled = true;
      btnRemoveRec.textContent = '…  Removing';
      try {
        const res = await postRemove(info.name, info.site, 'recorder');
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = 'Removed from Recorder';
          setTimeout(render, 400);
        } else {
          btnRemoveRec.disabled = false;
          btnRemoveRec.textContent = '✕  Remove from Recorder';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnRemoveRec.disabled = false;
        btnRemoveRec.textContent = '✕  Remove from Recorder';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Auto-Record toggle ─────────────────────────────────────────────────────
  const chkAuto = document.getElementById('chk-auto');
  if (chkAuto) {
    chkAuto.addEventListener('change', async () => {
      const fb = document.getElementById('fb');
      const want = chkAuto.checked;
      chkAuto.disabled = true;
      try {
        const res = await postAuto(info.name, info.site, want);
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = want ? 'Auto-record enabled' : 'Auto-record disabled';
        } else {
          chkAuto.checked = !want;
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        chkAuto.checked = !want;
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
      chkAuto.disabled = false;
    });
  }

  // ── Add to Saved Models ────────────────────────────────────────────────────
  const btnSaved = document.getElementById('btn-saved');
  if (btnSaved && !inSaved) {
    btnSaved.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnSaved.disabled = true;
      btnSaved.textContent = '…';
      try {
        const res = await postAdd(info.name, info.site, 'saved');
        if (res.ok) {
          btnSaved.textContent = '⭐  In Saved Models';
          fb.className = 'feedback ok';
          fb.textContent = 'Added successfully';
        } else {
          btnSaved.disabled = false;
          btnSaved.textContent = '⭐  Add to Saved Models';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnSaved.disabled = false;
        btnSaved.textContent = '⭐  Add to Saved Models';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }
}

// ── Live polling ────────────────────────────────────────────────────────────
async function poll() {
  if (!pageInfo) return;   // no model on this page → nothing to track
  const appData = await fetchStatus(pageInfo.name, pageInfo.site);
  // Only re-render when the meaningful state changed — avoids flicker and
  // preserves transient button states ("…  Starting") between polls.
  if (stateSig(appData) !== lastSig) render(appData);
}

async function init() {
  pageInfo = await getPageModel();   // resolved once; the popup is tied to one tab
  await render();
  // Keep the popup in sync with the backend for as long as it stays open.
  setInterval(poll, POLL_MS);
}

init();
