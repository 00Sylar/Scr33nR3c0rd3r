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
import urllib.parse
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


# doppiocdn (Stripchat) rejects segment requests without a matching Referer.
_REFERERS = {
    "stripchat":  "https://stripchat.com/",
    "chaturbate": "https://chaturbate.com/",
    "camsoda":    "https://www.camsoda.com/",
}


def _fetch(url: str, mode: str = "chaturbate") -> requests.Response:
    headers = {"User-Agent": _user_agent}
    ref = _REFERERS.get(mode)
    if ref:
        headers["Referer"] = ref
        headers["Origin"] = ref.rstrip("/")
    last_exc = None
    for _ in range(3):
        try:
            return _session.get(url, timeout=20, headers=headers)
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def _wrap_url(url: str, mode: str = "chaturbate") -> str:
    # ffmpeg's hls demuxer whitelists segment URLs by *path* extension. Keep
    # .m3u8 for playlists; normalize every other (segment/init) extension to
    # .m4s — a universally-whitelisted fragmented-MP4 extension — so odd
    # upstream extensions (e.g. Camsoda's .fmp4) aren't rejected.
    upath = urllib.parse.urlparse(url).path
    last = upath.rsplit("/", 1)[-1].lower()
    ext = ".m3u8" if last.endswith(".m3u8") else ".m4s"
    return (
        f"http://127.0.0.1:{_port}/p{ext}"
        f"?m={mode}&u={urllib.parse.quote(url, safe='')}"
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


def _rewrite_playlist(text: str, base_url: str, mode: str = "chaturbate") -> str:
    if mode == "stripchat":
        # Resolve MOUFLON segment URLs / strip MOUFLON tags first, then wrap.
        import stripchat_native
        text = stripchat_native.rewrite_playlist(text)
    elif mode in ("chaturbate", "camsoda"):
        # Pin the highest-bitrate variant on the master playlist.
        text = _select_highest_variant(text)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif mode in ("chaturbate", "camsoda") and s.startswith(_LL_TAGS):
            continue
        elif s.startswith("#"):
            out.append(
                re.sub(
                    r'URI="([^"]+)"',
                    lambda m: 'URI="%s"'
                    % _wrap_url(urllib.parse.urljoin(base_url, m.group(1)), mode),
                    line,
                )
            )
        else:
            out.append(_wrap_url(urllib.parse.urljoin(base_url, s), mode))
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
        if not parsed.path.startswith("/p") or not target:
            self.send_error(404)
            return
        try:
            r = _fetch(target, mode)
        except Exception:
            self.send_error(502)
            return
        body = r.content
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        if target.split("?")[0].endswith(".m3u8") or "mpegurl" in ctype.lower():
            body = _rewrite_playlist(
                body.decode("utf-8", "replace"), target, mode
            ).encode("utf-8")
            ctype = "application/vnd.apple.mpegurl"
        try:
            self.send_response(r.status_code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # ffmpeg dropped the connection; nothing to do


def wrap(url: str, user_agent: str | None = None,
         mode: str = "chaturbate") -> str:
    """Start the relay (once) and return a localhost URL proxying `url`.
    `mode` selects the playlist transform: "chaturbate" or "stripchat"."""
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
    return _wrap_url(url, mode)
