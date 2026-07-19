const API = 'http://localhost:5200';  // v2 (TEST) port

// Site match patterns — keep in sync with manifest host_permissions/content_scripts
const SITE_PATTERNS = [
  '*://*.chaturbate.com/*',
  '*://*.stripchat.com/*',
  '*://*.camsoda.com/*',
  '*://*.myfreecams.com/*',
];

// ── API token ──────────────────────────────────────────────────────────────────
// Same shared secret the popup stores (Scr33nX Settings → Local API).
let TOKEN = '';
const tokenReady = chrome.storage.local.get('apiToken')
  .then((st) => { TOKEN = (st && st.apiToken) || ''; })
  .catch(() => {});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.apiToken) {
    TOKEN = changes.apiToken.newValue || '';
    refreshAll(true);
  }
});

function authHeaders() {
  return TOKEN ? { 'X-Api-Token': TOKEN } : {};
}

// ── URL → model info ───────────────────────────────────────────────────────────
// Same rules as the popup's extractModelInfo, but from a URL string so it can
// run in the worker (no page injection needed).
const SKIP = ['tags', 'search', 'following', 'discover', 'login', 'register',
              'promo', 'affiliates', 'p', 'trending', 'new-cams', 'female', 'male'];

function modelFromUrl(urlStr) {
  let u;
  try { u = new URL(urlStr); } catch { return null; }
  const host = u.hostname.replace('www.', '');
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
    let h = u.hash.replace(/^#\/?/, '');
    if (h.startsWith('model/')) h = h.slice('model/'.length);
    name = h.split(/[/?]/).filter(Boolean)[0] || '';
    if (!name) name = u.pathname.split('/').filter(Boolean).pop() || '';
  } else {
    name = u.pathname.split('/').filter(Boolean)[0] || '';
  }
  if (!name || SKIP.includes(name.toLowerCase())) return null;
  return { name: name.toLowerCase(), site };
}

// ── /models cache ──────────────────────────────────────────────────────────────
// One bulk call feeds the toolbar badge, every tab and every content script;
// a short TTL keeps listing pages with dozens of thumbnails cheap.
const CACHE_MS = 5000;
let cache = { t: 0, data: null };

async function getModels(force) {
  if (!force && Date.now() - cache.t < CACHE_MS) return cache.data;
  await tokenReady;
  let data = null;
  try {
    const r = await fetch(`${API}/models`, {
      headers: authHeaders(), signal: AbortSignal.timeout(2000),
    });
    if (r.ok) {
      const j = await r.json();
      if (j && j.ok) data = j;
    }
  } catch {}
  cache = { t: Date.now(), data };
  return data;
}

function findModel(data, info) {
  if (!data || !info) return null;
  return data.models.find((m) => m.name === info.name && m.site === info.site) || null;
}

// ── Badges ─────────────────────────────────────────────────────────────────────
// Global badge (no tabId) = number of active recordings; per-tab overrides show
// the state of the model page in that tab. Non-model tabs fall back to global.

async function updateGlobalBadge(data) {
  const n = data ? data.recording : 0;
  try {
    await chrome.action.setBadgeBackgroundColor({ color: '#d32f2f' });
    await chrome.action.setBadgeText({ text: n > 0 ? String(n) : '' });
  } catch {}
}

async function updateTabBadge(tabId, url, data) {
  const m = findModel(data, modelFromUrl(url || ''));
  try {
    if (!m) {
      // Unknown model or non-model page — clear the override, global shows.
      await chrome.action.setBadgeText({ tabId, text: null });
      await chrome.action.setTitle({ tabId, title: null });
      return;
    }
    let text, color, state;
    if (m.status === 'recording')      { text = 'REC'; color = '#d32f2f'; state = 'recording'; }
    else if (m.linked_recording)       { text = 'REC'; color = '#ff8f00';
      state = `recording on ${m.linked_recording.site} as ${m.linked_recording.name}`; }
    else if (m.status === 'online')    { text = 'ON';  color = '#2e7d32'; state = 'online'; }
    else if (m.in_recorder)            { text = 'OFF'; color = '#546e7a'; state = m.status || 'idle'; }
    else                               { text = '★';   color = '#1565c0'; state = 'saved'; }
    await chrome.action.setBadgeBackgroundColor({ tabId, color });
    await chrome.action.setBadgeText({ tabId, text });
    await chrome.action.setTitle({ tabId, title: `Scr33nX — ${m.name}: ${state}` });
  } catch {}
}

// Refresh the global badge + every open cam-site tab.
async function refreshAll(force) {
  const data = await getModels(force);
  await updateGlobalBadge(data);
  let tabs = [];
  try { tabs = await chrome.tabs.query({ url: SITE_PATTERNS }); } catch {}
  for (const t of tabs) await updateTabBadge(t.id, t.url, data);
}

// ── Tab events ─────────────────────────────────────────────────────────────────

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  let tab;
  try { tab = await chrome.tabs.get(tabId); } catch { return; }
  const data = await getModels();
  await updateGlobalBadge(data);
  await updateTabBadge(tabId, tab.url, data);
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  // url covers SPA hash changes (MFC); status covers normal navigations.
  if (!changeInfo.url && changeInfo.status !== 'complete') return;
  if (!modelFromUrl(tab.url || '')) {
    try { await chrome.action.setBadgeText({ tabId, text: null }); } catch {}
    return;
  }
  await updateTabBadge(tabId, tab.url, await getModels());
});

// ── Periodic refresh ───────────────────────────────────────────────────────────
// Catches recordings that start/stop while you're not interacting (auto-record,
// web UI actions). 30s is the MV3 alarm floor.
chrome.alarms.create('scr33nx-poll', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'scr33nx-poll') refreshAll(true);
});

// ── Messages (content scripts + popup) ─────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'models') {
    getModels().then((data) => sendResponse({ models: data ? data.models : null }));
    return true;  // async response
  }
  if (msg && msg.type === 'refresh') {
    // Popup performed an action (add/start/stop) — resync badges right away.
    refreshAll(true);
  }
});

// ── Context menu: add models without opening the tab ───────────────────────────

function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'scr33nx-add-rec', title: 'Add to Scr33nX Recorder',
      contexts: ['link'], targetUrlPatterns: SITE_PATTERNS,
    });
    chrome.contextMenus.create({
      id: 'scr33nx-add-saved', title: 'Add to Scr33nX Saved Models',
      contexts: ['link'], targetUrlPatterns: SITE_PATTERNS,
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info) => {
  const m = modelFromUrl(info.linkUrl || '');
  if (!m) return;
  const target = info.menuItemId === 'scr33nx-add-saved' ? 'saved' : 'recorder';
  await tokenReady;
  try {
    await fetch(`${API}/add`, {
      method:  'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body:    JSON.stringify({ name: m.name, site: m.site, target }),
      signal:  AbortSignal.timeout(3000),
    });
  } catch {}
  refreshAll(true);   // the listing badge appears on the next content sweep
});

// ── Startup ────────────────────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => { createMenus(); refreshAll(true); });
chrome.runtime.onStartup.addListener(() => { createMenus(); refreshAll(true); });
