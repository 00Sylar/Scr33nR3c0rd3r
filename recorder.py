"""
recorder.py — Core stream recording engine
Stripchat: custom HLS downloader using media-hls.doppiocdn.com
Chaturbate: ffmpeg direct recording
"""

import os
import re
import sys
import glob
import json
import time
import shutil
import threading
import subprocess
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

# Shared session for connection pooling — reduces socket churn under many models
_http = requests.Session()
_http.headers.update(HEADERS)


class ModelStatus(Enum):
    OFFLINE   = "offline"
    ONLINE    = "online"
    RECORDING = "recording"
    ERROR     = "error"
    CHECKING  = "checking"
    PRIVATE   = "private"   # in ticket/private/group show — not publicly streaming


@dataclass
class RecordingSession:
    model_name: str
    site: str
    output_dir: str
    max_size_mb: Optional[int]
    stream_url: str
    part: int = 1
    process: Optional[subprocess.Popen] = None
    start_time: Optional[float] = None
    current_file: Optional[str] = None
    stopped: bool = False
    last_size: int = 0              # last known file size (bytes)
    last_size_change: float = 0.0   # timestamp when size last grew


@dataclass
class ModelConfig:
    name: str
    site: str
    status: ModelStatus = ModelStatus.OFFLINE
    session: Optional[RecordingSession] = None
    last_checked: float = 0
    error_message: str = ""
    stream_url: str = ""
    restart_count: int = 0
    groups: set = field(default_factory=set)  # {"recorder", "saved"}


# ── Chaturbate ────────────────────────────────────────────────────────────────

_CB_OFFLINE_STATUSES   = {"offline", "away", "private", "hidden", ""}
_CB_API_LOCK           = threading.Lock()  # serialise all CB API calls
_CB_LAST_API_CALL: float = 0.0             # timestamp of last CB HTTP request
_CB_MIN_CALL_INTERVAL  = 1.5              # seconds between consecutive CB requests


def _fetch_chaturbate_once(model_name: str) -> tuple[Optional[str], str]:
    """Single attempt. Returns (hls_url_or_None, room_status).
    room_status == 'rate_limited' means Cloudflare returned an empty/blocked response.
    """
    global _CB_LAST_API_CALL
    # Serialise all CB API calls — 1.5 s minimum gap prevents concurrent hammering
    with _CB_API_LOCK:
        wait = _CB_MIN_CALL_INTERVAL - (time.time() - _CB_LAST_API_CALL)
        if wait > 0:
            time.sleep(wait)
        _CB_LAST_API_CALL = time.time()

    r = _http.get(
        f"https://chaturbate.com/api/chatvideocontext/{model_name}/",
        timeout=12,
    )
    if r.status_code == 404:
        return None, "offline"
    if not r.content or r.status_code in (429, 403, 503):
        return None, "rate_limited"
    data = r.json()
    hls = data.get("hls_source") or data.get("stream_url") or ""
    room = data.get("room_status") or ""
    return (hls.strip() or None), room.strip()


def get_chaturbate_stream_url(model_name: str, max_retries: int = 1) -> Optional[str]:
    """
    Fetch the HLS stream URL for a Chaturbate model.

    max_retries controls CDN-warmup retries (room=public but no URL yet):
      1  — monitor path: fast, move on if not ready
      4  — manual REC path: persistent, gives CDN time to serve the URL
    Rate-limit responses (empty body / 429) bail immediately without retrying.
    """
    try:
        room = ""
        for attempt in range(max_retries + 1):
            hls, room = _fetch_chaturbate_once(model_name)
            if hls:
                if attempt:
                    logger.debug(f"[CB] {model_name}: got URL on attempt {attempt + 1}")
                return hls
            if room == "rate_limited":
                # Silent fallback — old-version monitor treated this as offline.
                logger.debug(f"[CB] {model_name}: rate-limited by Cloudflare")
                return None
            if room in _CB_OFFLINE_STATUSES:
                return None
            if attempt < max_retries:
                # room=public but no URL — CDN warmup, retry is valid
                logger.debug(f"[CB] {model_name}: room={room!r} no URL yet — retry {attempt + 1}/{max_retries} in 2 s")
                time.sleep(2)

        logger.debug(f"[CB] {model_name}: exhausted retries (room={room!r})")
        return None
    except Exception as e:
        logger.error(f"[CB] {model_name}: {e}")
        return None


# ── Stripchat ─────────────────────────────────────────────────────────────────

def get_stripchat_stream_url(model_name: str) -> Optional[str]:
    """
    Confirmed working approach from browser network inspection:
    1. Page embeds window.__PRELOADED_STATE__ with model id + isLive
    2. streamName == model numeric ID (e.g. "171032946")
    3. Master playlist: https://media-hls.doppiocdn.com/hls/{id}/master_{id}.m3u8
    4. Master playlist contains variant playlists with pkey parameter
    5. Variant URL format: /b-hls-{N}/{id}/{id}_{quality}p.m3u8?...&pkey={key}
    """
    page_headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Referer": "https://stripchat.com/",
    }

    try:
        resp = _http.get(
            f"https://stripchat.com/{model_name}",
            headers=page_headers, timeout=20
        )
        if resp.status_code not in (200,):
            return None

        html = resp.text

        # Quick live check
        if not re.search(r'"isLive"\s*:\s*true', html, re.IGNORECASE):
            return None

        # Get stream name (= model numeric ID)
        stream_names = re.findall(r'"streamName"\s*:\s*"(\d+)"', html)
        if not stream_names:
            # Try from __PRELOADED_STATE__
            m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.+)', html, re.DOTALL)
            if m:
                raw = m.group(1)
                end = raw.find('</script>')
                if end != -1:
                    raw = raw[:end].rstrip('; \t\n\r')
                try:
                    raw = re.sub(r'\\u[0-9A-F]{4}', lambda x: x.group(0).lower(), raw)
                    data = json.loads(raw)
                    mid = str(data.get("viewCam", {}).get("model", {}).get("id", ""))
                    if mid:
                        stream_names = [mid]
                except Exception:
                    pass

        if not stream_names:
            ids = re.findall(r'"id"\s*:\s*(\d{7,9})', html)
            if ids:
                stream_names = [ids[0]]

        if not stream_names:
            logger.warning(f"[ST] No stream name found for {model_name}")
            return None

        stream_id = stream_names[0]
        logger.debug(f"[ST] {model_name} stream_id={stream_id}")

        # Return the master playlist URL — the downloader will resolve pkey
        master_url = f"https://media-hls.doppiocdn.com/hls/{stream_id}/master_{stream_id}.m3u8"
        return master_url

    except Exception as e:
        logger.error(f"[ST] Error for {model_name}: {e}")
        return None


def get_camsoda_stream_url(model_name: str) -> Optional[str]:
    """
    Camsoda live HLS resolver.
    Public endpoint:  https://www.camsoda.com/api/v1/video/vtoken/<name>
    Response JSON:    { "token": "...", "edge_servers": ["host/path"], "stream_name": "...", "status": "online" }
    Builds: https://{edge}/{stream_name}_v1/index.m3u8?token={token}
    (edge already includes its path segment; stream_name embeds the resolution.)
    """
    api_url = f"https://www.camsoda.com/api/v1/video/vtoken/{model_name}"
    try:
        r = _http.get(api_url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        token = data.get("token")
        edges = data.get("edge_servers") or []
        stream_name = data.get("stream_name")
        status = (data.get("status") or "").lower()
        if not (token and edges and stream_name):
            return None
        if status and status != "online":
            return None
        edge = edges[0]
        return f"https://{edge}/{stream_name}_v1/index.m3u8?token={token}"
    except Exception as e:
        logger.error(f"[CS] {model_name}: {e}")
        return None


def get_chaturbate_online_rooms(should_continue: Optional[Callable[[], bool]] = None,
                                on_progress: Optional[Callable[[int, int], None]] = None) -> Optional[set]:
    """
    Fetch usernames of ALL publicly online Chaturbate rooms via the paginated
    room-list API (max 90 rooms/page, ~100 pages for the whole site). One full
    sweep covers any number of saved models in ~2.5 min at the 1.5 s cadence —
    vs one request per model, which Cloudflare rate-limits into false OFFLINEs.

    on_progress(rooms_fetched, total_rooms) is called every ~25 pages.
    Returns a set of lowercase usernames, or None on failure/abort so callers
    keep previous statuses instead of marking everything offline.
    """
    global _CB_LAST_API_CALL
    rooms: set = set()
    offset = 0
    total = 0
    pages = 0
    while True:
        if should_continue and not should_continue():
            return None
        with _CB_API_LOCK:
            wait = _CB_MIN_CALL_INTERVAL - (time.time() - _CB_LAST_API_CALL)
            if wait > 0:
                time.sleep(wait)
            _CB_LAST_API_CALL = time.time()
        try:
            r = _http.get(
                "https://chaturbate.com/api/ts/roomlist/room-list/",
                params={"limit": 90, "offset": offset}, timeout=15,
            )
            if r.status_code != 200 or not r.content:
                logger.warning(f"[CB] room-list HTTP {r.status_code} at offset {offset}")
                return None
            data = r.json()
        except Exception as e:
            logger.error(f"[CB] room-list fetch failed at offset {offset}: {e}")
            return None
        page = data.get("rooms") or []
        if not total:
            total = int(data.get("total_count") or 0)
        for room in page:
            u = (room.get("username") or "").lower()
            if u:
                rooms.add(u)
        offset += len(page)
        pages += 1
        if on_progress and (pages == 10 or pages % 25 == 0):
            on_progress(offset, total)
        if not page or (total and offset >= total):
            return rooms


def _stripchat_is_live(model_name: str) -> Optional[bool]:
    """Lightweight online check for the saved-models scanner.
    Returns True/False, or None when the page couldn't be fetched
    (so the caller keeps the previous status)."""
    page_headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Referer": "https://stripchat.com/",
    }
    try:
        resp = _http.get(
            f"https://stripchat.com/{model_name}",
            headers=page_headers, timeout=20
        )
        if resp.status_code == 404:
            return False
        if resp.status_code != 200:
            return None
        return bool(re.search(r'"isLive"\s*:\s*true', resp.text, re.IGNORECASE))
    except Exception as e:
        logger.error(f"[ST] live-check error for {model_name}: {e}")
        return None


def get_stream_url(site: str, model_name: str, thorough: bool = False) -> Optional[str]:
    if site == "chaturbate":
        return get_chaturbate_stream_url(model_name, max_retries=4 if thorough else 1)
    elif site == "stripchat":
        return get_stripchat_stream_url(model_name)
    elif site == "camsoda":
        return get_camsoda_stream_url(model_name)
    return None


# ── FFmpeg ────────────────────────────────────────────────────────────────────

def find_ffmpeg(override: str = "") -> str:
    """Resolve an absolute path to ffmpeg.exe by checking the filesystem.
    No probe subprocess: spawning `ffmpeg -version` can fail transiently
    (post-boot churn, Defender first-scan, console-less pythonw quirks) and
    used to make the monitor refuse to start even though ffmpeg was fine.
    On a miss, the error lists every candidate checked — never a mystery."""
    here  = os.path.dirname(os.path.abspath(__file__))
    local = os.environ.get("LOCALAPPDATA", "")

    def candidates():
        if override:
            yield "settings ffmpeg_path", override
        yield "app folder", os.path.join(here, "ffmpeg", "ffmpeg.exe")
        yield "app folder", os.path.join(here, "ffmpeg.exe")
        yield "PATH", shutil.which("ffmpeg") or "(no 'ffmpeg' on PATH)"
        if local:
            yield "WinGet links", os.path.join(
                local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")
            for hit in sorted(glob.glob(os.path.join(
                    local, "Microsoft", "WinGet", "Packages",
                    "*FFmpeg*", "**", "bin", "ffmpeg.exe"), recursive=True)):
                yield "WinGet package", hit

    checked = []
    for label, path in candidates():
        if os.path.isfile(path):
            return os.path.abspath(path)
        checked.append(f"{path} [{label}]")
    detail = "; ".join(checked)
    logger.error("ffmpeg not found. Checked: %s", detail)
    raise FileNotFoundError(
        f"ffmpeg not found. Checked: {detail}. Install it "
        f"(winget install Gyan.FFmpeg) or set ffmpeg_path in "
        f"~/.streamrecorder_config.json")


SITE_TAGS = {"chaturbate": "CB", "stripchat": "ST", "camsoda": "CS"}


def build_output_path(session: RecordingSession) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    site_tag = SITE_TAGS.get(session.site, session.site[:2].upper())
    part_tag = f"_part{session.part:03d}" if session.max_size_mb else ""
    return os.path.join(session.output_dir,
                        f"{session.model_name}_{site_tag}_{ts}{part_tag}.ts")


def _popen_ffmpeg(cmd: list) -> subprocess.Popen:
    """Launch ffmpeg with stdin pipe so we can request a graceful 'q' shutdown.
    On Windows we also create a new process group so CTRL_BREAK_EVENT is an option."""
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags = 0
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def graceful_stop(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """Tell ffmpeg to finish cleanly so the MPEG-TS trailer is flushed
    (prevents corrupted .ts files). Falls back to terminate/kill."""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def launch_ffmpeg_hls(stream_url: str, output_path: str, ffmpeg_path: str,
                      site: str = "", label: str = "") -> subprocess.Popen:
    """Launch ffmpeg to record an HLS stream.
    Adds flags that prevent corrupt-packet propagation and reconnect on drop —
    fixes truncated .ts files seen in the wild."""
    headers_map = {
        "stripchat": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://stripchat.com\r\nReferer: https://stripchat.com/\r\n"
        ),
        "chaturbate": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://chaturbate.com\r\nReferer: https://chaturbate.com/\r\n"
        ),
        "camsoda": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://www.camsoda.com\r\nReferer: https://www.camsoda.com/\r\n"
        ),
    }
    headers = headers_map.get(site, f"User-Agent: {USER_AGENT}\r\n")
    if site in ("chaturbate", "camsoda"):
        # Route through the local relay: it pins the highest-bitrate variant
        # and (for CB) survives the edge's mid-segment TLS resets. Plain HTTP
        # to 127.0.0.1; requests fetches upstream reliably.
        import cb_relay
        stream_url = cb_relay.wrap(stream_url, USER_AGENT, mode=site,
                                   label=label)
        headers = ""
    cmd = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error",
    ]
    if headers:
        cmd += ["-headers", headers]
    cmd += [
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
    ]
    if site in ("chaturbate", "camsoda"):
        # Relay segment URLs may use extensions outside ffmpeg's HLS default
        # whitelist (e.g. Camsoda's .fmp4) — accept them all.
        cmd += ["-allowed_extensions", "ALL"]
    if site == "chaturbate":
        cmd += ["-m3u8_hold_counters", "20"]
    cmd += [
        "-i", stream_url,
        "-c", "copy",
        "-copyts",
        output_path,
    ]
    return _popen_ffmpeg(cmd)


def launch_stripchat_playwright(model_name: str, output_path: str) -> subprocess.Popen:
    """
    Spawn the browser-based Stripchat recorder (stripchat_live.py) as a
    subprocess. It behaves like a Popen ffmpeg process: writes a live-growing
    .ts file at output_path, honors stdin-close as a graceful shutdown signal.

    Why browser-based: Stripchat's MOUFLON DRM encrypts variant-playlist URIs
    so ffmpeg-over-HLS hangs. Headless Chromium decodes them for us.
    """
    script = os.path.join(os.path.dirname(__file__), "stripchat_live.py")
    cmd = [sys.executable, script, model_name, output_path]
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags = 0
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def launch_stripchat_native(model_name: str, output_path: str,
                            ffmpeg_path: str) -> Optional[subprocess.Popen]:
    """Browserless Stripchat path: resolve the MOUFLON-keyed variant, serve it
    through the local relay, and record with plain ffmpeg -c copy (light,
    single-quality). Returns None if the native path can't be used (model not
    public, keys rotated, advert loop) — caller should fall back to Playwright.
    """
    import stripchat_native
    import cb_relay
    try:
        keyed = stripchat_native.resolve(model_name)
    except Exception:
        return None
    if not keyed:
        return None
    relay_url = cb_relay.wrap(keyed, USER_AGENT, mode="stripchat",
                              label=f"stripchat:{model_name}")
    return launch_ffmpeg_hls(relay_url, output_path, ffmpeg_path,
                             site="stripchat")


# ── StreamRecorder ────────────────────────────────────────────────────────────

class StreamRecorder:
    def __init__(self):
        self.models: dict[str, ModelConfig] = {}
        self.ffmpeg_path: str = ""
        self.output_dir: str  = os.path.expanduser("~/Videos/StreamRecorder")
        self.max_size_mb: Optional[int] = None
        self.check_interval: int = 30
        self.browser: str = "brave"

        self.on_status_change: Optional[Callable[[str, ModelStatus, str], None]] = None
        self.on_notification:  Optional[Callable[[str, str], None]] = None
        self.on_log:           Optional[Callable[[str], None]] = None

        # Relay reports segments that expired before they could be downloaded
        # (bandwidth saturated). Always logged; notification is opt-out.
        self.gap_warnings_enabled: bool = True
        self._gap_warn_ts: dict[str, float] = {}
        import cb_relay
        cb_relay.set_gap_callback(self._on_relay_gap)

        self._lock    = threading.Lock()
        # Per-group monitor flags — one thread per group ("recorder", "saved")
        self._running: dict[str, bool] = {"recorder": False, "saved": False}
        # Always-on session watcher — handles split/stall/exit even when the
        # monitor is off (e.g. user clicked REC without starting monitoring).
        self._session_watcher_started = False

    def _on_relay_gap(self, label: str, missed: int, seconds: float):
        """Relay callback (prefetch thread): segments expired unfetched."""
        self._log(f"⚠ {label}: {missed} segment(s) (~{seconds:.0f}s) lost — "
                  f"download can't keep up with the live stream")
        if not self.gap_warnings_enabled or not self.on_notification:
            return
        now = time.time()
        if now - self._gap_warn_ts.get(label, 0.0) < 60:
            return  # at most one toast per stream per minute
        self._gap_warn_ts[label] = now
        self.on_notification(
            "Dropped segments",
            f"{label}: ~{seconds:.0f}s of video lost — your internet "
            f"bandwidth can't keep up with all active recordings.")

    def add_model(self, name: str, site: str, group: str = "recorder"):
        key = f"{site}:{name.lower()}"
        with self._lock:
            cfg = self.models.get(key)
            if cfg is None:
                cfg = ModelConfig(name=name.lower(), site=site)
                self.models[key] = cfg
            cfg.groups.add(group)
        self._log(f"Added {site}/{name} [{group}]")

    def remove_model(self, name: str, site: str, group: str = "recorder"):
        key = f"{site}:{name.lower()}"
        killed = None
        with self._lock:
            cfg = self.models.get(key)
            if not cfg:
                return
            cfg.groups.discard(group)
            if cfg.groups:
                self._log(f"Removed {site}/{name} [{group}] (still in {cfg.groups})")
                return
            self.models.pop(key, None)
            killed = cfg.session
            cfg.session = None
        if killed:
            # Session is already detached — flush it in the background so
            # GUI-thread callers don't freeze on graceful_stop.
            threading.Thread(target=self._kill_session, args=(killed,),
                             daemon=True, name=f"kill-{key}").start()
        self._log(f"Removed {site}/{name}")

    def start_monitor(self, group: Optional[str] = None) -> bool:
        """Start monitor thread(s). With no arg, starts both 'recorder' and 'saved'.
        Returns False when startup failed (so the GUI doesn't show MONITORING)."""
        groups = [group] if group else ["recorder", "saved"]
        try:
            self.ffmpeg_path = find_ffmpeg()
            self._log(f"ffmpeg: {self.ffmpeg_path}")
        except FileNotFoundError as e:
            self._log(f"ERROR: {e}")
            if self.on_notification:
                self.on_notification("FFmpeg Missing", str(e))
            return False
        os.makedirs(self.output_dir, exist_ok=True)
        for g in groups:
            if self._running.get(g):
                continue
            self._running[g] = True
            threading.Thread(target=self._monitor_loop, args=(g,),
                             daemon=True, name=f"mon-{g}").start()
            self._log(f"Monitor [{g}] started.")
        return True

    def stop_monitor(self, group: Optional[str] = None):
        """Stop monitor thread(s) and kill their sessions. With no arg, stops both."""
        groups = [group] if group else ["recorder", "saved"]
        for g in groups:
            self._running[g] = False
        with self._lock:
            victims = []
            for cfg in self.models.values():
                if not cfg.session:
                    continue
                # Only kill sessions whose groups are ALL being stopped
                # (so a shared-group model keeps recording under the other monitor)
                if cfg.groups.issubset(set(groups)) or not cfg.groups:
                    victims.append(cfg.session)
                    cfg.session = None
        # Kill OUTSIDE the lock (so monitor/GUI threads aren't blocked) and in
        # parallel — graceful_stop waits up to ~15 s per process, so a serial
        # loop over many recordings froze the app for minutes.
        threads = [
            threading.Thread(target=self._kill_session, args=(s,), daemon=True)
            for s in victims
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        for g in groups:
            self._log(f"Monitor [{g}] stopped.")

    def start_recording(self, name: str, site: str) -> bool:
        key = f"{site}:{name.lower()}"
        with self._lock:
            cfg = self.models.get(key)
            if not cfg:
                return False
            if cfg.session:
                self._log(f"Already recording {name}")
                return False
            self._set_status(cfg, ModelStatus.CHECKING, "")

        # Ensure ffmpeg is available (may not be set if monitor is off)
        if not self.ffmpeg_path:
            try:
                self.ffmpeg_path = find_ffmpeg()
                self._log(f"ffmpeg: {self.ffmpeg_path}")
            except FileNotFoundError as e:
                self._log(f"ERROR: {e}")
                with self._lock:
                    self._set_status(cfg, ModelStatus.ERROR, str(e))
                return False
        os.makedirs(self.output_dir, exist_ok=True)

        self._log(f"Checking if {name} ({site}) is online...")
        url = get_stream_url(site, name, thorough=True)
        if not url:
            with self._lock:
                self._set_status(cfg, ModelStatus.OFFLINE, "")
            self._log(f"{name} ({site}) is offline — cannot record.")
            return False
        with self._lock:
            cfg.stream_url = url
        self._begin_recording(cfg, url)
        return True

    def stop_recording(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        with self._lock:
            cfg = self.models.get(key)
            if not cfg or not cfg.session:
                return
            session = cfg.session
            cfg.session = None
            # Always set OFFLINE after explicit stop to avoid triggering
            # auto-rec again in the GUI (user intentionally stopped)
            cfg.stream_url = ""
            self._set_status(cfg, ModelStatus.OFFLINE, "")
        self._kill_session(session)
        self._log(f"Stopped recording {name} ({site})")

    def _monitor_loop(self, group: str):
        """Old-style sequential monitor: one pass per tick, in-loop session
        management, 5s base cadence, per-model check_interval gating.

        The 'saved' group uses a bulk scanner instead — per-model checks don't
        scale to watchlists with hundreds of models (Cloudflare rate-limits the
        hammering and everything reports as a false OFFLINE)."""
        try:
            if group == "saved":
                self._saved_monitor_loop()
                return
            while self._running.get(group):
                with self._lock:
                    configs = [c for c in self.models.values() if group in c.groups]
                for cfg in configs:
                    if not self._running.get(group):
                        break
                    # One bad model/session must never kill the whole loop
                    try:
                        self._monitor_check_one(cfg)
                    except Exception:
                        logger.exception(f"[mon-{group}] check failed for "
                                         f"{cfg.site}/{cfg.name}")
                time.sleep(5)
        except Exception as e:
            logger.exception(f"Monitor [{group}] crashed")
            self._log(f"Monitor [{group}] CRASHED: {e!r} — see ~/.streamrecorder.log")
        finally:
            self._running[group] = False

    def _monitor_check_one(self, cfg: ModelConfig):
        """One recorder-loop pass for a single model: session housekeeping +
        (when due) an online check."""
        now = time.time()
        if cfg.session:
            self._check_split(cfg)
            if cfg.session and cfg.session.process:
                if cfg.session.process.poll() is not None:
                    self._handle_ffmpeg_exit(cfg)
                else:
                    self._check_stall(cfg)
        if now - cfg.last_checked < self.check_interval:
            return
        cfg.last_checked = now
        if cfg.session:
            return
        self._set_status(cfg, ModelStatus.CHECKING, "")
        url = get_stream_url(cfg.site, cfg.name)
        # Re-check session after slow network call — auto-rec may
        # have started a recording while we were fetching the URL
        if cfg.session:
            return
        if url:
            cfg.stream_url = url
            self._set_status(cfg, ModelStatus.ONLINE, "")
        else:
            cfg.stream_url = ""
            self._set_status(cfg, ModelStatus.OFFLINE, "")

    def _launch_proc(self, cfg: ModelConfig, output_path: str,
                     stream_url: str) -> Optional[subprocess.Popen]:
        """Start the recording process for a model. Stripchat tries the
        browserless native path first and falls back to Playwright; other
        sites use ffmpeg directly."""
        if cfg.site == "stripchat":
            proc = launch_stripchat_native(cfg.name, output_path,
                                           self.ffmpeg_path)
            if proc is not None:
                self._log(f"{cfg.name}: native HLS path (no browser)")
                return proc
            self._log(f"{cfg.name}: native path unavailable — browser fallback")
            return launch_stripchat_playwright(cfg.name, output_path)
        return launch_ffmpeg_hls(stream_url, output_path, self.ffmpeg_path,
                                 site=cfg.site,
                                 label=f"{cfg.site}:{cfg.name}")

    def _saved_monitor_loop(self):
        """Saved-group monitor: 5s session housekeeping tick + a bulk status
        scan every check_interval, run in a worker thread so housekeeping
        stays responsive during the multi-minute sweep."""
        try:
            last_scan = 0.0
            scan_thread: Optional[threading.Thread] = None
            while self._running.get("saved"):
                with self._lock:
                    configs = [c for c in self.models.values() if "saved" in c.groups]
                for cfg in configs:
                    if not self._running.get("saved"):
                        break
                    if not cfg.session:
                        continue
                    try:
                        self._check_split(cfg)
                        if cfg.session and cfg.session.process:
                            if cfg.session.process.poll() is not None:
                                self._handle_ffmpeg_exit(cfg)
                            else:
                                self._check_stall(cfg)
                    except Exception:
                        logger.exception(f"[mon-saved] housekeeping failed for "
                                         f"{cfg.site}/{cfg.name}")
                now = time.time()
                if ((scan_thread is None or not scan_thread.is_alive())
                        and now - last_scan >= self.check_interval):
                    last_scan = now
                    scan_thread = threading.Thread(target=self._scan_saved_pass,
                                                   daemon=True, name="saved-scan")
                    scan_thread.start()
                time.sleep(5)
        except Exception as e:
            logger.exception("Monitor [saved] crashed")
            self._log(f"Monitor [saved] CRASHED: {e!r} — see ~/.streamrecorder.log")
        finally:
            self._running["saved"] = False

    def _scan_saved_pass(self):
        try:
            self._scan_saved_pass_inner()
        except Exception as e:
            logger.exception("Saved scan crashed")
            self._log(f"Saved scan CRASHED: {e!r} — see ~/.streamrecorder.log")

    def _scan_saved_pass_inner(self):
        """One bulk status pass over all saved-group models:
        - Chaturbate: one room-list sweep, membership test (no per-model calls)
        - Stripchat/Camsoda: per-model checks through a small thread pool
        Models with an active session are skipped; statuses only change on a
        definitive online/offline answer (failures keep the previous status)."""
        running = lambda: self._running.get("saved", False)
        t0 = time.time()
        with self._lock:
            configs = [c for c in self.models.values() if "saved" in c.groups]
        cb = [c for c in configs if c.site == "chaturbate"]
        others = [c for c in configs if c.site in ("stripchat", "camsoda")]
        if not configs:
            return
        self._log(f"Saved scan started ({len(configs)} models)…")
        counts = {"chaturbate": 0, "stripchat": 0, "camsoda": 0}

        # Stripchat/Camsoda per-model checks run CONCURRENTLY with the long
        # Chaturbate sweep so the first statuses appear within seconds
        def scan_others():
            def check(cfg: ModelConfig):
                if cfg.site == "stripchat":
                    return cfg, _stripchat_is_live(cfg.name)
                return cfg, (get_camsoda_stream_url(cfg.name) is not None)
            try:
                self._log(f"Saved scan: checking {len(others)} "
                          f"Stripchat/Camsoda models…")
                done = 0
                with ThreadPoolExecutor(max_workers=6,
                                        thread_name_prefix="saved-scan") as pool:
                    futures = [pool.submit(check, c) for c in others]
                    for fut in as_completed(futures):
                        if not running():
                            pool.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            cfg, live = fut.result()
                        except Exception:
                            logger.exception("[saved-scan] SC/CS check failed")
                            continue
                        done += 1
                        if done % 150 == 0:
                            self._log(f"Saved scan: Stripchat/Camsoda "
                                      f"{done}/{len(others)} checked…")
                        if live is None:
                            continue  # fetch failed — keep previous status
                        counts[cfg.site] += live
                        self._apply_scan_status(cfg, live)
            except Exception as e:
                logger.exception("Saved scan (Stripchat/Camsoda) crashed")
                self._log(f"Saved scan (SC/CS) CRASHED: {e!r} "
                          f"— see ~/.streamrecorder.log")

        t_others = None
        if others and running():
            t_others = threading.Thread(target=scan_others, daemon=True,
                                        name="saved-scan-others")
            t_others.start()

        if cb and running():
            rooms = get_chaturbate_online_rooms(
                running,
                lambda got, total: self._log(
                    f"Saved scan: Chaturbate sweep {got}/{total} rooms…"))
            if rooms is None:
                if running():
                    self._log("Saved scan: Chaturbate room list unavailable "
                              "(rate-limited?) — keeping previous statuses.")
            else:
                for cfg in cb:
                    online = cfg.name in rooms
                    counts["chaturbate"] += online
                    self._apply_scan_status(cfg, online)
                self._log(f"Saved scan: Chaturbate done — "
                          f"{counts['chaturbate']}/{len(cb)} online.")

        if t_others is not None:
            t_others.join()

        if running():
            n_sc = sum(1 for c in others if c.site == "stripchat")
            n_cs = len(others) - n_sc
            parts = []
            if cb:
                parts.append(f"CB {counts['chaturbate']}/{len(cb)} online")
            if n_sc:
                parts.append(f"SC {counts['stripchat']}/{n_sc} online")
            if n_cs:
                parts.append(f"CS {counts['camsoda']}/{n_cs} online")
            self._log(f"Saved scan done: {', '.join(parts)} "
                      f"({time.time() - t0:.0f}s)")

    def _apply_scan_status(self, cfg: ModelConfig, online: bool):
        with self._lock:
            if cfg.session:
                return
            new = ModelStatus.ONLINE if online else ModelStatus.OFFLINE
            if not online:
                cfg.stream_url = ""
            if cfg.status != new:
                self._set_status(cfg, new, "")

    def _begin_recording(self, cfg: ModelConfig, stream_url: str):
        session = RecordingSession(
            model_name=cfg.name, site=cfg.site,
            output_dir=self.output_dir, max_size_mb=self.max_size_mb,
            stream_url=stream_url,
        )
        output_path          = build_output_path(session)
        session.current_file = output_path
        session.start_time   = time.time()
        session.last_size_change = time.time()

        try:
            proc = self._launch_proc(cfg, output_path, stream_url)

            if proc is None:
                self._set_status(cfg, ModelStatus.ERROR, "Could not get stream URL")
                return

            # Log stderr in background
            def _log_stderr(p=proc, name=cfg.name):
                try:
                    for line in p.stderr:
                        decoded = line.decode('utf-8', errors='replace').strip()
                        if decoded:
                            self._log(f"[ffmpeg/{name}] {decoded}")
                except Exception:
                    pass
            threading.Thread(target=_log_stderr, daemon=True).start()

            session.process = proc
            cfg.session     = session
            self._set_status(cfg, ModelStatus.RECORDING, output_path)
            self._ensure_session_watcher()
            self._log(f"Recording {cfg.site}/{cfg.name} → {output_path}")
            if self.on_notification:
                self.on_notification("Recording Started",
                                     f"{cfg.name} ({cfg.site}) is now recording.")
        except Exception as e:
            self._set_status(cfg, ModelStatus.ERROR, str(e))
            self._log(f"Recording failed for {cfg.name}: {e}")

    def _ensure_session_watcher(self):
        """Lazy-start the always-on session watcher on the first recording."""
        if self._session_watcher_started:
            return
        self._session_watcher_started = True
        threading.Thread(target=self._session_watch_loop,
                         daemon=True, name="session-watcher").start()
        self._log("Session watcher started.")

    def _session_watch_loop(self):
        """Handles split, stall detection, and ffmpeg-exit → OFFLINE
        transitions for any active session WHEN THE MONITOR IS OFF.
        When the monitor is running it already does these checks, so this
        loop idles to avoid duplicated work and race conditions."""
        while True:
            time.sleep(60)
            # Skip entirely while any monitor is running — it handles sessions
            if any(self._running.values()):
                continue
            with self._lock:
                active = [c for c in self.models.values() if c.session]
            for cfg in active:
                if not cfg.session:
                    continue
                try:
                    self._check_split(cfg)
                    if cfg.session and cfg.session.process:
                        if cfg.session.process.poll() is not None:
                            self._handle_ffmpeg_exit(cfg)
                        else:
                            self._check_stall(cfg)
                except Exception as e:
                    self._log(f"session-watcher error for {cfg.name}: {e}")

    def _check_split(self, cfg: ModelConfig):
        session = cfg.session
        if not session or not session.max_size_mb or not session.current_file:
            return
        try:
            size_mb = os.path.getsize(session.current_file) / (1024 * 1024)
        except OSError:
            return
        if size_mb < session.max_size_mb:
            return
        self._log(f"Split {cfg.name}: {size_mb:.0f}/{session.max_size_mb} MB")
        # Graceful stop so the .ts trailer flushes before we open the next part
        graceful_stop(session.process, timeout=10)
        session.part += 1
        url = get_stream_url(cfg.site, cfg.name) or session.stream_url
        if url:
            output_path          = build_output_path(session)
            session.current_file = output_path
            session.stream_url   = url
            session.last_size    = 0
            session.last_size_change = time.time()
            try:
                proc = self._launch_proc(cfg, output_path, url)
                if proc:
                    session.process = proc
                    self._set_status(cfg, ModelStatus.RECORDING, output_path)
                    self._log(f"Part {session.part} → {output_path}")
            except Exception as e:
                self._set_status(cfg, ModelStatus.ERROR, str(e))
        else:
            cfg.session = None
            self._set_status(cfg, ModelStatus.OFFLINE, "")

    def _check_stall(self, cfg: ModelConfig):
        """Kill ffmpeg if the output file hasn't grown for 60 seconds."""
        session = cfg.session
        if not session or not session.current_file:
            return
        try:
            size = os.path.getsize(session.current_file)
        except OSError:
            return
        now = time.time()
        if size > session.last_size:
            session.last_size = size
            session.last_size_change = now
            return
        # Allow 60s grace period (stream buffering, brief interruptions)
        stall_secs = now - session.last_size_change
        if stall_secs < 60:
            return
        self._log(f"Stall detected for {cfg.name} — no data for {stall_secs:.0f}s, flushing ffmpeg")
        graceful_stop(session.process, timeout=5)
        # _handle_ffmpeg_exit will run on the next loop iteration
        # and handle restart logic

    def _handle_ffmpeg_exit(self, cfg: ModelConfig):
        if not cfg.session:
            return
        rc  = cfg.session.process.returncode if cfg.session.process else -1
        url = cfg.session.stream_url
        self._log(f"ffmpeg exited for {cfg.name} (rc={rc})")

        # Stripchat: rc 4 = idle (no segments), rc 5 = ticket/private/group show
        # Model is online but not publicly broadcasting — show PRIVATE, no restart.
        if cfg.site == "stripchat" and rc in (4, 5):
            # Clean up stale .ts (usually 0-byte or moov-only, unplayable)
            try:
                if (cfg.session.current_file
                        and os.path.exists(cfg.session.current_file)
                        and os.path.getsize(cfg.session.current_file) < 64 * 1024):
                    os.remove(cfg.session.current_file)
            except OSError:
                pass
            cfg.restart_count = 0
            cfg.session       = None
            cfg.stream_url    = ""
            # 5-minute cooldown so the monitor doesn't immediately flip back to ONLINE
            cfg.last_checked  = time.time() + 300
            label = "ticket/private show" if rc == 5 else "no public segments"
            self._log(f"{cfg.name} ({cfg.site}) is in {label} — status=PRIVATE, retry in 5 min")
            self._set_status(cfg, ModelStatus.PRIVATE, "")
            return

        if rc in (0, 1) and cfg.restart_count < 3:
            cfg.restart_count += 1
            self._log(f"Auto-restarting {cfg.name} (attempt {cfg.restart_count}/3)...")
            cfg.session = None
            # Push last_checked forward to prevent monitor loop from
            # re-checking this model during the restart delay window
            cfg.last_checked = time.time() + 10

            def _delayed_restart(c=cfg, u=url):
                time.sleep(3)
                if not self._running:
                    return
                # Another recording may have been started while we waited
                if c.session:
                    return
                new_url = get_stream_url(c.site, c.name) or u
                if new_url:
                    c.stream_url = new_url
                    self._begin_recording(c, new_url)
                else:
                    c.restart_count = 0
                    self._set_status(c, ModelStatus.OFFLINE, "")
            threading.Thread(target=_delayed_restart, daemon=True).start()
            return

        cfg.restart_count = 0
        cfg.session    = None
        cfg.stream_url = ""
        self._set_status(cfg, ModelStatus.OFFLINE, "")
        if self.on_notification:
            self.on_notification("Recording Stopped",
                                 f"{cfg.name} ({cfg.site}) stream ended.")

    def _kill_session(self, session: RecordingSession):
        session.stopped = True
        # Graceful 'q' flush so the MPEG-TS trailer is written — fixes the
        # corrupted .ts files observed on prior recordings.
        graceful_stop(session.process, timeout=10)

    def _set_status(self, cfg: ModelConfig, status: ModelStatus, detail: str):
        cfg.status        = status
        cfg.error_message = detail if status == ModelStatus.ERROR else ""
        if self.on_status_change:
            self.on_status_change(f"{cfg.site}:{cfg.name}", status, detail)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(msg)
        if self.on_log:
            self.on_log(f"[{ts}] {msg}")
