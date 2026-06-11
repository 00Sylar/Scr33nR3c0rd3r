"""
cb_relay.py — local HTTP relay for site HLS streams.

Chaturbate's llhls CDN edges reset ffmpeg's TLS connections mid-segment
(ffmpeg/Schannel "The specified session has been invalidated", error -10054),
which truncates fMP4 segments and corrupts recordings. Python's requests
stack downloads the same segments reliably, so ffmpeg is pointed at this
relay on 127.0.0.1 (plain HTTP) and the relay fetches upstream via requests.

Playlists are rewritten so every URI (segments, EXT-X-MAP, EXT-X-MEDIA,
EXT-X-PART) also goes through the relay.

A per-stream `mode` (carried in each wrapped URL) selects the playlist
transform: "chaturbate" strips LL-HLS partial-segment tags; "stripchat"
applies MOUFLON decryption (stripchat_native.rewrite_playlist) so plain
ffmpeg can record Stripchat without a browser.
"""

import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_port: int | None = None
_user_agent = _DEFAULT_UA
_session = requests.Session()
# All streams hit the same CDN host; the urllib3 default of 10 pooled
# connections per host forces fresh TLS handshakes under concurrent load.
_adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=64)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


# ── Bandwidth accounting ──────────────────────────────────────────────────────
# Every byte fetched upstream is counted here; the app polls bytes_downloaded()
# to drive the bandwidth meter.

_bytes_lock = threading.Lock()
_bytes_total = 0


def _count(n: int):
    global _bytes_total
    with _bytes_lock:
        _bytes_total += n


def bytes_downloaded() -> int:
    """Total bytes fetched from upstream CDNs since process start."""
    with _bytes_lock:
        return _bytes_total


# ── Segment prefetch cache ────────────────────────────────────────────────────
# ffmpeg's hls demuxer downloads segments one at a time; if a download takes
# longer than the segment duration the demuxer falls permanently behind and
# segments expire from the live window (1.6 s × N jumps in the output). The
# relay already rewrites every media playlist, so it knows upcoming segment
# URLs before ffmpeg asks: fetch them in parallel into a small in-memory
# cache and serve ffmpeg's requests instantly, letting slow streams catch up.

_PREFETCH_WORKERS = 16
_CACHE_MAX_BYTES = 300 * 1024 * 1024   # hard cap; beyond it, fall back to miss path
_ENTRY_TTL = 90.0                      # s: drop fetched segments never requested
_FETCHED_TTL = 600.0                   # s: how long to remember served URLs

_executor = ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS,
                               thread_name_prefix="relay-prefetch")
_state_lock = threading.Lock()
_cache: dict[str, "_Entry"] = {}       # upstream URL → in-flight/ready segment
_fetched: dict[str, float] = {}        # upstream URL → ts of successful fetch
_streams: dict[str, dict] = {}         # stream key → {"segs": [...], "dur": avg}

# Called as fn(stream_label, missed_segments, est_seconds_lost) whenever
# segments expire from the live playlist before they could be downloaded.
_gap_cb = None


def set_gap_callback(fn):
    global _gap_cb
    _gap_cb = fn


class _Entry:
    __slots__ = ("event", "data", "ctype", "status", "ts", "error")

    def __init__(self):
        self.event = threading.Event()
        self.data = b""
        self.ctype = "application/octet-stream"
        self.status = 200
        self.ts = time.monotonic()
        self.error = False


def _prefetch_one(url: str, mode: str, entry: _Entry):
    try:
        r = _fetch(url, mode)
        entry.data = r.content
        entry.status = r.status_code
        entry.ctype = r.headers.get("Content-Type", "application/octet-stream")
        _count(len(entry.data))
        if 200 <= r.status_code < 300:
            with _state_lock:
                _fetched[url] = time.monotonic()
        else:
            entry.error = True
    except Exception:
        entry.error = True
    finally:
        entry.ts = time.monotonic()
        entry.event.set()
        if entry.error:
            # Drop failed entries so the next playlist refresh retries them.
            with _state_lock:
                _cache.pop(url, None)


def _track_media_playlist(text: str, base_url: str, mode: str, key: str):
    """Scan a media playlist: prefetch every segment not yet handled, detect
    segments that expired before we could fetch them, and prune the cache."""
    segs, durs = [], []
    last_dur = 0.0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXTINF"):
            try:
                last_dur = float(s.split(":", 1)[1].split(",")[0])
            except (ValueError, IndexError):
                last_dur = 0.0
        elif s and not s.startswith("#"):
            segs.append(urllib.parse.urljoin(base_url, s))
            durs.append(last_dur)
    if not segs:
        return  # master playlist or empty — nothing to do
    known = [d for d in durs if d > 0]
    avg = (sum(known) / len(known)) if known else 2.0

    now = time.monotonic()
    submit, missed = [], 0
    with _state_lock:
        st = _streams.setdefault(key, {"segs": []})
        new = set(segs)
        # A URL from the previous refresh that vanished without ever being
        # fetched (no cache entry, not in _fetched) expired upstream → gap.
        for u in st["segs"]:
            if u not in new and u not in _fetched and u not in _cache:
                missed += 1
        st["segs"] = segs
        st["dur"] = avg

        total = sum(len(e.data) for e in _cache.values())
        if total < _CACHE_MAX_BYTES:
            for u in segs:
                if u not in _cache and u not in _fetched:
                    e = _Entry()
                    _cache[u] = e
                    submit.append((u, e))
        for u, e in list(_cache.items()):
            if e.event.is_set() and now - e.ts > _ENTRY_TTL:
                _cache.pop(u, None)
        for u, t in list(_fetched.items()):
            if now - t > _FETCHED_TTL:
                _fetched.pop(u, None)
    for u, e in submit:
        _executor.submit(_prefetch_one, u, mode, e)
    if missed and _gap_cb:
        try:
            _gap_cb(key, missed, missed * avg)
        except Exception:
            pass


# doppiocdn (Stripchat) rejects segment requests without a matching Referer.
_REFERERS = {
    "stripchat":  "https://stripchat.com/",
    "chaturbate": "https://chaturbate.com/",
    "camsoda":    "https://www.camsoda.com/",
    "myfreecams": "https://www.myfreecams.com/",
}


def _headers(mode: str) -> dict:
    headers = {"User-Agent": _user_agent}
    ref = _REFERERS.get(mode)
    if ref:
        headers["Referer"] = ref
        headers["Origin"] = ref.rstrip("/")
    return headers


def _fetch(url: str, mode: str = "chaturbate") -> requests.Response:
    headers = _headers(mode)
    last_exc = None
    for _ in range(3):
        try:
            return _session.get(url, timeout=20, headers=headers)
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def _wrap_url(url: str, mode: str = "chaturbate", label: str = "") -> str:
    # ffmpeg's hls demuxer whitelists segment URLs by *path* extension. Keep
    # .m3u8 for playlists; normalize every other (segment/init) extension to
    # .m4s — a universally-whitelisted fragmented-MP4 extension — so odd
    # upstream extensions (e.g. Camsoda's .fmp4) aren't rejected.
    upath = urllib.parse.urlparse(url).path
    last = upath.rsplit("/", 1)[-1].lower()
    ext = ".m3u8" if last.endswith(".m3u8") else ".m4s"
    extra = f"&l={urllib.parse.quote(label, safe='')}" if label else ""
    return (
        f"http://127.0.0.1:{_port}/p{ext}"
        f"?m={mode}{extra}&u={urllib.parse.quote(url, safe='')}"
    )


# LL-HLS-only tags: stripped so ffmpeg records full segments only and never
# mixes partial segments (which duplicate/overlap data) into the output.
_LL_TAGS = (
    "#EXT-X-PART",          # also matches #EXT-X-PART-INF
    "#EXT-X-PRELOAD-HINT",
    "#EXT-X-RENDITION-REPORT",
    "#EXT-X-SERVER-CONTROL",
)


def _select_highest_variant(text: str) -> str:
    """For a master playlist, keep only the highest-BANDWIDTH video variant
    (and its referenced audio rendition) so ffmpeg can't fall back to a lower
    bitrate. Media playlists (no EXT-X-STREAM-INF) pass through unchanged."""
    lines = text.splitlines()
    best_i, best_bw = -1, -1
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else 0
            if bw > best_bw:
                best_i, best_bw = i, bw
    if best_i < 0:
        return text  # not a master playlist
    inf = lines[best_i]
    url = lines[best_i + 1] if best_i + 1 < len(lines) else ""
    am = re.search(r'AUDIO="([^"]+)"', inf)
    audio_group = am.group(1) if am else None

    out = []
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF") or line.startswith("#EXT-X-MEDIA:"):
            continue  # drop all variant/rendition declarations; re-add chosen below
        s = line.strip()
        if s and not s.startswith("#"):
            continue  # drop variant URL lines
        out.append(line)
    # Re-add the chosen audio rendition (if any) then the chosen video variant.
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:") and audio_group and \
                f'GROUP-ID="{audio_group}"' in line:
            out.append(line)
    out.append(inf)
    out.append(url)
    return "\n".join(out) + "\n"


def _rewrite_playlist(text: str, base_url: str, mode: str = "chaturbate",
                      label: str = "") -> str:
    if mode == "stripchat":
        # Resolve MOUFLON segment URLs / strip MOUFLON tags first, then wrap.
        import stripchat_native
        text = stripchat_native.rewrite_playlist(text)
    elif mode in ("chaturbate", "camsoda", "myfreecams"):
        # Pin the highest-bitrate variant on the master playlist.
        text = _select_highest_variant(text)
    # Kick off parallel segment prefetch (no-op for master playlists).
    _track_media_playlist(text, base_url, mode,
                          label or urllib.parse.urlparse(base_url).path)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif mode in ("chaturbate", "camsoda", "myfreecams") and s.startswith(_LL_TAGS):
            continue
        elif s.startswith("#"):
            out.append(
                re.sub(
                    r'URI="([^"]+)"',
                    lambda m: 'URI="%s"'
                    % _wrap_url(urllib.parse.urljoin(base_url, m.group(1)),
                                mode, label),
                    line,
                )
            )
        else:
            out.append(_wrap_url(urllib.parse.urljoin(base_url, s), mode, label))
    return "\n".join(out) + "\n"


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # ffmpeg resets idle keep-alive sockets; not an error for us


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence per-request console spam
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("u", [None])[0]
        mode = qs.get("m", ["chaturbate"])[0]
        label = qs.get("l", [""])[0]
        if not parsed.path.startswith("/p") or not target:
            self.send_error(404)
            return

        is_playlist = parsed.path.endswith(".m3u8")
        if not is_playlist:
            # Segment: usually already prefetched (or in flight) — serve from
            # cache so ffmpeg never waits on the upstream CDN.
            with _state_lock:
                entry = _cache.get(target)
            if entry is not None:
                entry.event.wait(timeout=30)
                if entry.event.is_set() and not entry.error:
                    with _state_lock:
                        _cache.pop(target, None)  # ffmpeg fetches each URL once
                    self._send(entry.status, entry.ctype, entry.data)
                    return
            self._proxy_stream(target, mode)
            return

        try:
            r = _fetch(target, mode)
        except Exception:
            self.send_error(502)
            return
        _count(len(r.content))
        body = _rewrite_playlist(
            r.content.decode("utf-8", "replace"), target, mode, label
        ).encode("utf-8")
        self._send(r.status_code, "application/vnd.apple.mpegurl", body)

    def _send(self, status: int, ctype: str, body: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # ffmpeg dropped the connection; nothing to do

    def _proxy_stream(self, target: str, mode: str):
        """Cache miss: forward the segment, streaming chunks to ffmpeg as they
        arrive instead of buffering the whole segment first."""
        try:
            r = _session.get(target, timeout=20, headers=_headers(mode),
                             stream=True)
        except requests.RequestException:
            self.send_error(502)
            return
        try:
            clen = r.headers.get("Content-Length")
            self.send_response(r.status_code)
            self.send_header("Content-Type", r.headers.get(
                "Content-Type", "application/octet-stream"))
            if clen:
                self.send_header("Content-Length", clen)
            else:
                self.close_connection = True
            self.end_headers()
            for chunk in r.iter_content(65536):
                _count(len(chunk))
                self.wfile.write(chunk)
            if 200 <= r.status_code < 300:
                with _state_lock:
                    _fetched[target] = time.monotonic()
        except (ConnectionError, OSError, requests.RequestException):
            self.close_connection = True
        finally:
            r.close()


def wrap(url: str, user_agent: str | None = None,
         mode: str = "chaturbate", label: str = "") -> str:
    """Start the relay (once) and return a localhost URL proxying `url`.
    `mode` selects the playlist transform: "chaturbate" or "stripchat".
    `label` names the stream (e.g. "chaturbate:model") for gap reporting."""
    global _server, _port, _user_agent
    with _lock:
        if user_agent:
            _user_agent = user_agent
        if _server is None:
            _server = _QuietServer(("127.0.0.1", 0), _Handler)
            _port = _server.server_address[1]
            threading.Thread(
                target=_server.serve_forever, daemon=True,
                name="cb-relay",
            ).start()
    return _wrap_url(url, mode, label)
