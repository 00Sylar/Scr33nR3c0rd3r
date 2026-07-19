// Scr33nX listing badges — overlays a small status badge on model thumbnails
// so you can see who is recording / tracked without opening the tab.
// Data comes from the background worker (single cached /models call).
(() => {
  const host = location.hostname.replace('www.', '');
  let site = null;
  if (host.includes('chaturbate.com')) site = 'chaturbate';
  else if (host.includes('stripchat.com')) site = 'stripchat';
  else if (host.includes('camsoda.com'))    site = 'camsoda';
  else if (host.includes('myfreecams.com')) site = 'myfreecams';
  if (!site) return;

  const SKIP = ['tags', 'search', 'following', 'discover', 'login', 'register',
                'promo', 'affiliates', 'p', 'trending', 'new-cams', 'female', 'male'];

  // name → model entry for this site; null until the first response arrives
  let known = null;

  const STYLE = `
    .scr33nx-badge {
      position: absolute; top: 4px; left: 4px; z-index: 2147483000;
      padding: 1px 5px; border-radius: 3px; pointer-events: none;
      font: bold 10px/14px Arial, sans-serif; color: #fff;
      box-shadow: 0 1px 2px rgba(0,0,0,.55);
    }
    .scr33nx-rec    { background: #d32f2f; }
    .scr33nx-lnkrec { background: #ff8f00; }
    .scr33nx-on     { background: #2e7d32; }
    .scr33nx-idle   { background: #546e7a; }
    .scr33nx-saved  { background: #1565c0; }
    @keyframes scr33nx-pulse { 50% { opacity: .45; } }
    .scr33nx-rec, .scr33nx-lnkrec { animation: scr33nx-pulse 1.2s ease-in-out infinite; }
  `;
  const styleEl = document.createElement('style');
  styleEl.textContent = STYLE;
  document.documentElement.appendChild(styleEl);

  // Model name from a card link href (same first-segment rule as the popup;
  // MFC links may carry the name in the hash instead).
  function nameFromHref(href) {
    let u;
    try { u = new URL(href, location.href); } catch { return null; }
    if (!u.hostname.replace('www.', '').includes(host)) return null;
    let name;
    if (site === 'myfreecams' && u.hash) {
      let h = u.hash.replace(/^#\/?/, '');
      if (h.startsWith('model/')) h = h.slice('model/'.length);
      name = h.split(/[/?]/).filter(Boolean)[0] || '';
    } else {
      const segs = u.pathname.split('/').filter(Boolean);
      if (segs.length !== 1) return null;   // deep paths are never model roots
      name = segs[0];
    }
    name = (name || '').toLowerCase();
    if (!name || SKIP.includes(name)) return null;
    if (!/^[a-z0-9_-]+$/.test(name)) return null;
    return name;
  }

  function badgeFor(m) {
    if (m.status === 'recording') return ['scr33nx-rec',   'REC',
      `Scr33nX: recording ${m.name}`];
    if (m.linked_recording)       return ['scr33nx-lnkrec', 'REC',
      `Scr33nX: already recording as ${m.linked_recording.name} on ${m.linked_recording.site}`];
    if (m.status === 'online')    return ['scr33nx-on',    'ON',
      `Scr33nX: ${m.name} online (in Recorder)`];
    if (m.in_recorder)            return ['scr33nx-idle',  'OFF',
      `Scr33nX: ${m.name} in Recorder (${m.status || 'idle'})`];
    return ['scr33nx-saved', '★', `Scr33nX: ${m.name} in Saved Models`];
  }

  function sweep() {
    if (!known) return;
    for (const a of document.querySelectorAll('a[href]')) {
      const name = nameFromHref(a.getAttribute('href'));
      const m = name ? known.get(name) : null;
      let badge = a.querySelector(':scope > .scr33nx-badge');
      if (!m) {
        if (badge) badge.remove();
        continue;
      }
      // Only badge links that look like cards (contain an image/preview);
      // plain text links (menus, chat mentions) stay clean.
      if (!badge && !a.querySelector('img, video, picture')) continue;
      if (!badge) {
        if (getComputedStyle(a).position === 'static') a.style.position = 'relative';
        badge = document.createElement('span');
        badge.className = 'scr33nx-badge';
        a.appendChild(badge);
      }
      const [cls, text, title] = badgeFor(m);
      const want = `scr33nx-badge ${cls}`;
      if (badge.className !== want) badge.className = want;
      if (badge.textContent !== text) badge.textContent = text;
      badge.title = title;   // pointer-events:none — shown via the card hover
      a.title = title;
    }
  }

  async function refresh() {
    let resp = null;
    try { resp = await browser.runtime.sendMessage({ type: 'models' }); } catch {}
    if (resp && resp.models) {
      known = new Map(resp.models.filter((m) => m.site === site)
                                 .map((m) => [m.name, m]));
    } else {
      known = null;                        // app down → drop stale badges
      document.querySelectorAll('.scr33nx-badge').forEach((b) => b.remove());
    }
    sweep();
  }

  // Re-sweep when infinite scroll / SPA navigation adds cards (debounced).
  let debounce = null;
  new MutationObserver((muts) => {
    if (muts.every((mu) => [...mu.addedNodes].every(
        (n) => n.nodeType !== 1 || n.classList?.contains('scr33nx-badge')))) return;
    clearTimeout(debounce);
    debounce = setTimeout(sweep, 500);
  }).observe(document.documentElement, { childList: true, subtree: true });

  refresh();
  setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 10000);
})();
