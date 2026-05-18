"""
recorder.py — Core stream recording engine
Stripchat: custom HLS downloader using media-hls.doppiocdn.com
Chaturbate: ffmpeg direct recording
"""

import os
import re
import sys
import json
import time
import threading
import subprocess
import requests
import logging
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
    Response JSON:    { "token": "...", "edge_servers": ["..."], "stream_name": "...", "app": "edge" }
    Builds: https://{edge}/{app}/{stream_name}_v1/index.m3u8?token={token}
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
        app = data.get("app") or "edge"
        if not (token and edges and stream_name):
            return None
        edge = edges[0]
        # Most Camsoda edges use "https://{edge}/{app}/{stream}_v1/index.m3u8?token={token}"
        return f"https://{edge}/{app}/{stream_name}_v1/index.m3u8?token={token}"
    except Exception as e:
        logger.error(f"[CS] {model_name}: {e}")
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

def find_ffmpeg() -> str:
    candidates = [
        os.path.join(os.path.dirname(__file__), "ffmpeg", "ffmpeg.exe"),
        os.path.join(os.path.dirname(__file__), "ffmpeg.exe"),
        "ffmpeg",
    ]
    for c in candidates:
        try:
            r = subprocess.run([c, "-version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return c
        except Exception:
            continue
    raise FileNotFoundError("ffmpeg not found. Place ffmpeg.exe in the StreamRecorder folder.")


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
                      site: str = "") -> subprocess.Popen:
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
    cmd = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error",
        "-headers", headers,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
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

        self._lock    = threading.Lock()
        # Per-group monitor flags — one thread per group ("recorder", "saved")
        self._running: dict[str, bool] = {"recorder": False, "saved": False}
        # Always-on session watcher — handles split/stall/exit even when the
        # monitor is off (e.g. user clicked REC without starting monitoring).
        self._session_watcher_started = False

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
            self._kill_session(killed)
        self._log(f"Removed {site}/{name}")

    def start_monitor(self, group: Optional[str] = None):
        """Start monitor thread(s). With no arg, starts both 'recorder' and 'saved'."""
        groups = [group] if group else ["recorder", "saved"]
        try:
            self.ffmpeg_path = find_ffmpeg()
            self._log(f"ffmpeg: {self.ffmpeg_path}")
        except FileNotFoundError as e:
            self._log(f"ERROR: {e}")
            if self.on_notification:
                self.on_notification("FFmpeg Missing", str(e))
            return
        os.makedirs(self.output_dir, exist_ok=True)
        for g in groups:
            if self._running.get(g):
                continue
            self._running[g] = True
            threading.Thread(target=self._monitor_loop, args=(g,),
                             daemon=True, name=f"mon-{g}").start()
            self._log(f"Monitor [{g}] started.")

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
                    victims.append(cfg)
            for cfg in victims:
                self._kill_session(cfg.session)
                cfg.session = None
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
        management, 5s base cadence, per-model check_interval gating."""
        while self._running.get(group):
            with self._lock:
                configs = [c for c in self.models.values() if group in c.groups]
            for cfg in configs:
                if not self._running.get(group):
                    break
                now = time.time()
                if cfg.session:
                    self._check_split(cfg)
                    if cfg.session and cfg.session.process:
                        if cfg.session.process.poll() is not None:
                            self._handle_ffmpeg_exit(cfg)
                        else:
                            self._check_stall(cfg)
                if now - cfg.last_checked < self.check_interval:
                    continue
                cfg.last_checked = now
                if cfg.session:
                    continue
                self._set_status(cfg, ModelStatus.CHECKING, "")
                url = get_stream_url(cfg.site, cfg.name)
                # Re-check session after slow network call — auto-rec may
                # have started a recording while we were fetching the URL
                if cfg.session:
                    continue
                if url:
                    cfg.stream_url = url
                    self._set_status(cfg, ModelStatus.ONLINE, "")
                else:
                    cfg.stream_url = ""
                    self._set_status(cfg, ModelStatus.OFFLINE, "")
            time.sleep(5)

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
            if cfg.site == "stripchat":
                proc = launch_stripchat_playwright(cfg.name, output_path)
            else:
                proc = launch_ffmpeg_hls(stream_url, output_path, self.ffmpeg_path,
                                         site=cfg.site)

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
                if cfg.site == "stripchat":
                    proc = launch_stripchat_playwright(cfg.name, output_path)
                else:
                    proc = launch_ffmpeg_hls(url, output_path, self.ffmpeg_path,
                                             site=cfg.site)
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
