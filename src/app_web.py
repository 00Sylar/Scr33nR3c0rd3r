"""
app_web.py — Scr33nX web UI (redesign) — native window shell.

Hosts the SAME engine as app.py (StreamRecorder, settings persistence, the
port-5200 control API, tray, pipeline, update check) but renders the
interface as HTML/CSS (src/webui/) inside a native pywebview window
(Windows WebView2 — no browser is involved). The classic Tk app stays
untouched and launchable.

Run with:  python src/app_web.py        (add --debug for devtools)
"""

import os
import sys
import json
import time
import queue
import shutil
import ctypes
import threading
import subprocess
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import HTTPServer
from typing import Optional

# Importing app reuses its module-level setup (rotating file log, crash
# handler) plus APP_VERSION and the HTTP API handler — so the extension/bot
# API behaves byte-identically. The Tk class is defined but never
# instantiated: no window, no Tk event loop.
import app as classic
from app import (_ApiHandler, APP_VERSION, GITHUB_REPO, _API_PORT,
                 QUALITY_OPTIONS, _quality_label, _detect_browsers)
import recorder as recorder_mod
import cb_relay
from recorder import StreamRecorder, ModelStatus
from settings import load_settings, save_settings, save_pipeline_settings
from notifier import send_notification

import logging
log = logging.getLogger("webui")

_SRC = os.path.dirname(os.path.abspath(__file__))

_STATUS_STR = {
    ModelStatus.OFFLINE:   "offline",
    ModelStatus.ONLINE:    "online",
    ModelStatus.RECORDING: "recording",
    ModelStatus.ERROR:     "error",
    ModelStatus.CHECKING:  "checking",
    ModelStatus.PRIVATE:   "private",
}

_DASH_SITES = [
    ("chaturbate", "CB"),
    ("stripchat",  "SC"),
    ("camsoda",    "CS"),
    ("myfreecams", "MFC"),
]

_SITES = ("chaturbate", "stripchat", "camsoda", "myfreecams")

_SITE_URLS = {
    "chaturbate": "https://chaturbate.com/{}/",
    "stripchat":  "https://stripchat.com/{}/",
    "camsoda":    "https://camsoda.com/{}/",
    "myfreecams": "https://myfreecams.com/#{}",
}


def parse_model_input(raw: str, fallback_site: str) -> tuple[str, str]:
    """Username or URL → (name, site). Same rules as the classic app."""
    raw = (raw or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    if raw.startswith("myfreecams.com"):
        frag = raw.split("#", 1)[1] if "#" in raw else ""
        frag = frag.lstrip("/")
        if frag.startswith("model/"):
            frag = frag[len("model/"):]
        return frag.split("/")[0].split("?")[0].strip("/"), "myfreecams"
    for domain, site in (("chaturbate.com", "chaturbate"),
                         ("stripchat.com",  "stripchat"),
                         ("camsoda.com",    "camsoda")):
        if raw.startswith(domain):
            parts = raw.split("/")
            return (parts[1] if len(parts) > 1 else "").strip("/"), site
    return raw.strip("/"), (fallback_site if fallback_site in _SITES
                            else "chaturbate")


def _fmt_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_073_741_824:.2f} GB"
    return f"{n / 1_048_576:.1f} MB"


def _set_clipboard(text: str) -> bool:
    """Unicode clipboard write via Win32 (no Tk available here)."""
    try:
        CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        data = text.encode("utf-16-le") + b"\x00\x00"
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            u32.SetClipboardData(CF_UNICODETEXT, h)
        finally:
            u32.CloseClipboard()
        return True
    except Exception:
        return False


class WebCore:
    """Headless engine host — the same boot/orchestration the Tk app does,
    minus every widget. Duck-types the attributes/methods _ApiHandler uses
    (after, recorder, _rows, _saved_data, _api_* …) so the HTTP API is the
    exact same code path as the classic app."""

    _LAUNCH_POOL_SIZE = 4    # same start-storm throttle as the classic app

    def __init__(self):
        self.settings = load_settings()
        self.recorder = StreamRecorder()
        self.recorder.output_dir     = self.settings.output_dir
        self.recorder.max_size_mb    = self.settings.max_size_mb
        self.recorder.check_interval = self.settings.check_interval
        self.recorder.on_status_change = self._cb_status
        self.recorder.on_log           = self._cb_log
        self.recorder.on_notification  = self._cb_notif
        self.recorder.gap_warnings_enabled = self.settings.gap_warnings_enabled

        self._rows: dict[str, bool] = {}
        self._saved_data: dict[str, dict] = {}
        self._auto_rec: dict[str, bool] = {}
        self._model_q: dict[str, int] = {}
        self._ranks: dict[str, int] = {
            k: int(v) for k, v in (self.settings.ranks or {}).items() if v}
        self.recorder.quality_global = self.settings.max_quality
        self.recorder.quality_overrides = self._model_q
        self.recorder.auto_downgrade_enabled = self.settings.auto_downgrade_enabled
        self.recorder.playwright_fallback_enabled = self.settings.playwright_fallback_enabled
        self.recorder.low_disk_guard_enabled = self.settings.low_disk_guard_enabled

        self._monitoring_recorder = False
        self._monitoring_saved    = False
        self.pipeline = None
        self.window = None
        self._tray = None
        self._closing = False
        self._saved_version = 1          # bumped on saved-list/rank changes

        self._rec_pool = ThreadPoolExecutor(max_workers=self._LAUNCH_POOL_SIZE,
                                            thread_name_prefix="rec-launch")
        self._launching: set[str] = set()
        self._launching_lock = threading.Lock()

        self._log_ring: deque = deque(maxlen=500)
        self._log_seq = 0
        self._log_lock = threading.Lock()
        self._pipe_ring: deque = deque(maxlen=400)
        self._pipe_seq = 0
        self._pipe_state = "stopped"
        self._pipe_lines = {"convert": "Convert:  idle",
                            "upload1": "Upload 1: idle",
                            "upload2": "Upload 2: idle"}
        self._prompt_evt: Optional[threading.Event] = None
        self._prompt_val = ""
        self._last_detail: dict[str, str] = {}
        self._size_cache: dict[str, int] = {}
        self._update_latest = ""
        self._bw_mbps = 0.0
        self._ul_mbps = 0.0

        self._tasks: queue.Queue = queue.Queue()
        threading.Thread(target=self._task_loop, daemon=True,
                         name="core-tasks").start()
        threading.Thread(target=self._meter_loop, daemon=True,
                         name="bw-meter").start()
        threading.Thread(target=self._size_loop, daemon=True,
                         name="size-sweep").start()
        threading.Thread(target=self._check_for_updates, daemon=True,
                         name="update-check").start()

        self._restore_models()
        self._start_api_server()
        if classic._is_cloud_synced(self.settings.output_dir):
            self._log_add("⚠ Output folder is inside a cloud-synced directory "
                          "(OneDrive/Dropbox) — sync uploads compete with "
                          "recording bandwidth and file locks can break "
                          "splitting. A local folder is strongly recommended.",
                          "warn")
        self._log_add(f"Scr33nX v{APP_VERSION} — web UI started "
                      f"({len(self._rows)} recorder model(s), "
                      f"{len(self._saved_data)} saved).", "accent")

    # ── Tk-compat scheduling shim ─────────────────────────────────────────────

    def after(self, delay_ms: int, fn):
        if delay_ms:
            t = threading.Timer(delay_ms / 1000.0, lambda: self._tasks.put(fn))
            t.daemon = True
            t.start()
        else:
            self._tasks.put(fn)

    def _task_loop(self):
        while True:
            fn = self._tasks.get()
            try:
                fn()
            except Exception:
                log.exception("core task failed")

    # ── Recorder callbacks (worker threads) ───────────────────────────────────

    def _cb_status(self, key: str, status: ModelStatus, detail: str):
        self._last_detail[key] = detail or ""
        if (status == ModelStatus.ONLINE and self._monitoring_recorder
                and self._auto_rec.get(key, False)):
            cfg = self.recorder.models.get(key)
            if not (cfg and cfg.session):
                site, name = key.split(":", 1)
                self._launch_recording(name, site)

    def _cb_log(self, msg: str):
        self._log_add(msg, "info", echo=False)

    def _cb_notif(self, title: str, msg: str):
        if self.settings.notifications_enabled:
            try:
                send_notification(title, msg)
            except Exception:
                pass

    def _launch_recording(self, name: str, site: str):
        key = f"{site}:{name}"
        with self._launching_lock:
            if key in self._launching:
                return
            self._launching.add(key)

        def _run():
            try:
                self.recorder.start_recording(name, site)
            except Exception as e:
                self._log_add(f"start_recording {key}: {e}", "error")
            finally:
                with self._launching_lock:
                    self._launching.discard(key)
        self._rec_pool.submit(_run)

    # ── Log rings ─────────────────────────────────────────────────────────────

    def _log_add(self, msg: str, kind: str = "info", echo: bool = True):
        with self._log_lock:
            self._log_seq += 1
            self._log_ring.append({"i": self._log_seq,
                                   "t": datetime.now().strftime("%H:%M:%S"),
                                   "m": str(msg), "k": kind})
        if echo:
            log.info("%s", msg)

    def _pipe_log_add(self, msg: str, kind: str = "info"):
        with self._log_lock:
            self._pipe_seq += 1
            self._pipe_ring.append({"i": self._pipe_seq,
                                    "t": datetime.now().strftime("%H:%M:%S"),
                                    "m": str(msg), "k": kind})

    # ── Identity helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _rank_key(name: str, site: str) -> str:
        return f"{site}:{name.lower()}"

    @staticmethod
    def _saved_key(name: str, site: str) -> str:
        return f"saved:{site}:{name.lower()}"

    def _get_rank(self, name: str, site: str) -> int:
        return int(self._ranks.get(self._rank_key(name, site), 0) or 0)

    # ── Persistence (same schema as the classic app) ──────────────────────────

    def _persist_models(self):
        self.settings.models = [
            {"name": k.split(":")[1], "site": k.split(":")[0],
             "auto_rec": self._auto_rec.get(k, False),
             "max_q": self._model_q.get(k, 0)}
            for k in self._rows
        ]
        self.settings.saved_models = [
            {"name": d["name"], "site": d["site"]}
            for d in self._saved_data.values()
        ]
        self.settings.ranks = {
            k: v for k, v in self._ranks.items()
            if v and (k in self._rows or f"saved:{k}" in self._saved_data)
        }
        save_settings(self.settings)

    def _restore_models(self):
        for m in self.settings.models:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self.recorder.add_model(n, s, "recorder")
                key = f"{s}:{n}"
                self._rows[key] = True
                self._auto_rec[key] = bool(m.get("auto_rec", False))
                q = int(m.get("max_q", 0) or 0)
                if q:
                    self._model_q[key] = q
        for m in self.settings.saved_models:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self._saved_data[self._saved_key(n, s)] = {"name": n, "site": s}

    # ── Model actions ─────────────────────────────────────────────────────────

    def _add_to_recorder(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        if key in self._rows:
            self._log_add(f"{name} ({site}) is already in the Recorder list.", "warn")
            return
        self.recorder.add_model(name, site, "recorder")
        self._rows[key] = True
        self._auto_rec.setdefault(key, False)
        self._persist_models()
        self._log_add(f"Added to Recorder: {name} ({site})", "accent")

    def _add_to_saved(self, name: str, site: str):
        sid = self._saved_key(name, site)
        if sid in self._saved_data:
            self._log_add(f"{name} ({site}) is already in Saved Models.", "warn")
            return
        self._saved_data[sid] = {"name": name, "site": site}
        if self._monitoring_saved:
            self.recorder.add_model(name, site, "saved")
        self._saved_version += 1
        self._persist_models()
        self._log_add(f"⭐ Added to Saved Models: {name} ({site})", "accent")

    def _remove_saved(self, sid: str, persist: bool = True):
        if sid not in self._saved_data:
            return
        self._saved_data.pop(sid, None)
        _, site, name = sid.split(":", 2)
        if f"{site}:{name}" not in self._rows:
            self._ranks.pop(self._rank_key(name, site), None)
        self.recorder.remove_model(name, site, "saved")
        self._saved_version += 1
        if persist:
            self._persist_models()
            self._log_add(f"Removed from Saved Models: {name} ({site})", "warn")

    def _do_remove_from_recorder(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        self.recorder.remove_model(name, site, "recorder")
        self._rows.pop(key, None)
        self._auto_rec.pop(key, None)
        self._model_q.pop(key, None)
        self._size_cache.pop(key, None)
        if self._saved_key(name, site) not in self._saved_data:
            self._ranks.pop(self._rank_key(name, site), None)
        self._persist_models()
        self._log_add(f"Removed model: {name} ({site})", "warn")

    def _set_auto(self, key: str, val: bool):
        self._auto_rec[key] = val
        self._persist_models()

    def _set_rank_many(self, items, rank: int) -> int:
        rank = max(0, min(5, int(rank)))
        changed = 0
        for name, site in items:
            k = self._rank_key(name, site)
            if int(self._ranks.get(k, 0) or 0) != rank:
                changed += 1
            if rank:
                self._ranks[k] = rank
            else:
                self._ranks.pop(k, None)
        if changed:
            self._saved_version += 1
            self._persist_models()
        return changed

    def _set_quality(self, keys: list, height: int):
        for key in keys:
            if height:
                self._model_q[key] = height
            else:
                self._model_q.pop(key, None)
        self._persist_models()
        names = ", ".join(k.split(":", 1)[1] for k in keys)
        lbl = _quality_label(height) if height else "Default"
        self._log_add(f"Max quality → {lbl}: {names} "
                      f"(applies on next recording start)", "accent")

    def _api_stop_all(self):
        for key in list(self._rows):
            self._auto_rec[key] = False
        self._persist_models()
        self._log_add("Stopping all downloads…", "warn")
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="stop-all-dl").start()

    def _do_clear_recorder(self, via_api: bool = False):
        if self._monitoring_recorder:
            self._toggle_monitor_recorder()
        for key in list(self._rows):
            self._auto_rec[key] = False
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="clear-stop-all").start()
        for key in list(self._rows):
            site, name = key.split(":", 1)
            self._do_remove_from_recorder(name, site)
        self._log_add("Recorder cleared%s." % (" (via API)" if via_api else ""),
                      "warn")

    def _toggle_monitor_recorder(self):
        if self._monitoring_recorder:
            self.recorder.stop_monitor("recorder")
            self._monitoring_recorder = False
            self._log_add("Recorder monitor stopped.", "warn")
        else:
            if self.recorder.start_monitor("recorder"):
                self._monitoring_recorder = True
                self._log_add(f"Recorder monitor started — polling "
                              f"{len(self._rows)} model(s) every "
                              f"{self.recorder.check_interval} s.", "accent")

    def _toggle_monitor_saved(self):
        if self._monitoring_saved:
            self.recorder.stop_monitor("saved")
            self._monitoring_saved = False
            self._log_add("Saved scanner stopped.", "warn")
        else:
            for d in self._saved_data.values():
                self.recorder.add_model(d["name"], d["site"], "saved", quiet=True)
            if self.recorder.start_monitor("saved"):
                self._monitoring_saved = True
                self._log_add(f"Saved scanner started — "
                              f"{len(self._saved_data)} model(s).", "accent")

    def _api_set_monitor(self, which: str, enabled: bool):
        current = (self._monitoring_recorder if which == "recorder"
                   else self._monitoring_saved)
        if enabled == current:
            return
        if which == "recorder":
            self._toggle_monitor_recorder()
        else:
            self._toggle_monitor_saved()

    # ── Pipeline (Telegram convert + upload) ──────────────────────────────────

    def _build_pipeline_config(self):
        from pipeline_worker import PipelineConfig
        cfg = PipelineConfig()
        s = self.settings
        try:
            cfg.api_id = int((s.telegram_api_id or "0").strip() or "0")
        except ValueError:
            cfg.api_id = 0
        cfg.api_hash = (s.telegram_api_hash or "").strip()
        cfg.phone = ""
        try:
            cfg.chat_id = int((s.telegram_group_id or "0").strip() or "0")
        except ValueError:
            cfg.chat_id = 0
        try:
            cfg.topic_id = int((s.telegram_topic_id or "0").strip() or "0")
        except ValueError:
            cfg.topic_id = 0
        cfg.do_convert = s.pipeline_do_convert
        cfg.do_upload = s.pipeline_do_upload
        cfg.watch_folder = s.output_dir
        cfg.output_folder = ((s.pipeline_converted_dir or "").strip()
                             or os.path.join(s.output_dir, "converted"))
        cfg.tdlib_dir = ((s.telegram_session_dir or "").strip()
                         or os.path.join(os.path.dirname(_SRC), "Pipeline", ".tdlib"))
        cfg.uploaded_log = os.path.join(cfg.tdlib_dir, "uploaded.txt")
        cfg.ffmpeg_path = self.recorder.ffmpeg_path or "ffmpeg"
        cfg.ffprobe_path = "ffprobe"
        return cfg

    def _toggle_pipeline(self, silent: bool = False):
        from pipeline_worker import PipelineWorker
        if self.pipeline and self.pipeline.running:
            self.pipeline.stop()
            self._pipe_state = "stopping"
            return
        cfg = self._build_pipeline_config()
        os.makedirs(cfg.output_folder, exist_ok=True)
        os.makedirs(cfg.tdlib_dir, exist_ok=True)
        self.pipeline = PipelineWorker(
            cfg,
            on_log=lambda line: self._pipe_log_add(line),
            on_state=lambda st: self.after(0, lambda: self._pipeline_state_changed(st)),
            on_progress=lambda k, n, p, s=0.0: self._pipeline_progress(k, n, p, s),
            prompt_cb=self._pipeline_prompt,
        )
        self.settings.pipeline_do_convert = cfg.do_convert
        self.settings.pipeline_do_upload  = cfg.do_upload
        save_pipeline_settings(self.settings)
        self.pipeline.start()
        self._pipe_state = "starting"
        self._pipe_log_add("Starting pipeline...", "accent")

    def _api_set_pipeline(self, enabled: bool):
        running = bool(self.pipeline and self.pipeline.running)
        if enabled == running:
            return
        self._toggle_pipeline(silent=True)

    def _api_set_pipeline_stage(self, convert=None, upload=None):
        if convert is not None:
            self.settings.pipeline_do_convert = bool(convert)
        if upload is not None:
            self.settings.pipeline_do_upload = bool(upload)
        save_pipeline_settings(self.settings)
        if self.pipeline and self.pipeline.running:
            cfg = self.pipeline.cfg
            if self.settings.pipeline_do_upload and not cfg.do_upload:
                fresh = self._build_pipeline_config()
                cfg.api_id, cfg.api_hash = fresh.api_id, fresh.api_hash
                cfg.chat_id, cfg.topic_id = fresh.chat_id, fresh.topic_id
            cfg.do_convert = self.settings.pipeline_do_convert
            cfg.do_upload = self.settings.pipeline_do_upload
            self._pipe_log_add(
                f"Stages updated live — convert: "
                f"{'on' if cfg.do_convert else 'off'}, "
                f"upload: {'on' if cfg.do_upload else 'off'}.", "accent")

    def _pipeline_state_changed(self, state: str):
        self._pipe_state = state
        if state in ("stopped", "error"):
            self._pipe_lines = {"convert": "Convert:  idle",
                                "upload1": "Upload 1: idle",
                                "upload2": "Upload 2: idle"}

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps <= 0:
            return ""
        if bps >= 1_048_576:
            return f"  {bps/1_048_576:.1f} MB/s"
        return f"  {bps/1024:.0f} KB/s"

    def _pipeline_progress(self, kind, name, pct, speed=0.0):
        bar_len = 22
        filled = int(round(bar_len * pct / 100.0))
        bar = "█" * filled + "░" * (bar_len - filled)
        short = name[:34]
        spd = self._fmt_speed(speed)
        if kind == "convert":
            idle = pct >= 100.0 and not name
            self._pipe_lines["convert"] = ("Convert:  idle" if idle
                else f"Convert:  [{bar}] {pct:5.1f}%  {short}")
        elif kind.startswith("upload"):
            slot = int(kind.replace("upload", "") or "1")
            slot = max(1, min(slot, 2))
            idle = pct >= 100.0 and not name
            self._pipe_lines[f"upload{slot}"] = (f"Upload {slot}: idle" if idle
                else f"Upload {slot}: [{bar}] {pct:5.1f}%{spd}  {short}")

    def _pipeline_prompt(self, label: str) -> str:
        """Worker thread — block until the page answers (or 5 min timeout)."""
        self._prompt_val = ""
        self._prompt_evt = threading.Event()
        try:
            self.window.evaluate_js(f"UI.pipePrompt({json.dumps(label)})")
        except Exception:
            return ""
        self._prompt_evt.wait(timeout=300)
        return self._prompt_val

    def _pipeline_reauth(self, confirmed: bool) -> dict:
        d = ((self.settings.telegram_session_dir or "").strip()
             or os.path.join(os.path.dirname(_SRC), "Pipeline", ".tdlib"))
        if not os.path.isdir(d):
            return {"ok": True, "existed": False,
                    "msg": "No TDLib session folder found — next Start will "
                           "begin a fresh login."}
        if not confirmed:
            return {"ok": True, "confirm": True, "dir": d}
        try:
            shutil.rmtree(d)
            self._pipe_log_add(f"Cleared session at {d}.", "warn")
            return {"ok": True, "existed": True, "msg": "Session cleared — "
                    "you'll log in again on next Start."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Preview ───────────────────────────────────────────────────────────────

    def _detect_player(self, kind: str):
        override = (self.settings.preview_player_path or "").strip()
        if override and os.path.isfile(override) and kind in os.path.basename(override).lower():
            return override
        if kind == "mpv":
            return shutil.which("mpv")
        if kind == "vlc":
            p = shutil.which("vlc")
            if p:
                return p
            for c in (r"%ProgramFiles%\VideoLAN\VLC\vlc.exe",
                      r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"):
                c = os.path.expandvars(c)
                if os.path.isfile(c):
                    return c
            return None
        if kind == "ffplay":
            ff = getattr(self.recorder, "ffmpeg_path", "") or ""
            if ff:
                cand = os.path.join(os.path.dirname(ff), "ffplay.exe")
                if os.path.isfile(cand):
                    return cand
            return shutil.which("ffplay")
        return None

    def _find_preview_player(self):
        engine = (self.settings.preview_engine or "auto").lower()
        if engine == "vlc":
            order = ["vlc", "mpv", "ffplay"]
        else:
            order = ["mpv", "vlc", "ffplay"]
        for kind in order:
            p = self._detect_player(kind)
            if p:
                return p, kind
        return None, None

    def preview_resolve(self, name: str, site: str) -> dict:
        """Blocking (called on a js_api thread): resolve upstream, wrap in the
        relay. External mode spawns the player; embedded returns the localhost
        URL for the in-page hls.js player."""
        key = f"{site}:{name.lower()}"
        cfg = self.recorder.models.get(key)
        status = cfg.status if cfg else None
        if status not in (ModelStatus.ONLINE, ModelStatus.RECORDING):
            return {"ok": False, "error": f"{name} ({site}) isn't online right "
                    "now — preview only works for online or recording models."}
        title = f"{name} ({site})"
        self._log_add(f"Preview: resolving {title}…")
        try:
            if site == "stripchat":
                import stripchat_native
                upstream = stripchat_native.resolve(name)
            else:
                upstream = recorder_mod.get_stream_url(site, name)
        except Exception as e:
            return {"ok": False, "error": f"Preview failed for {title}: {e}"}
        if not upstream:
            return {"ok": False, "error": f"Preview: couldn't resolve {title} "
                    + ("(Stripchat private/needs browser path?)"
                       if site == "stripchat" else "(offline?)")}
        try:
            url = cb_relay.wrap(upstream, recorder_mod.USER_AGENT, mode=site,
                                label=f"{site}:{name}")
        except Exception as e:
            return {"ok": False, "error": f"Preview relay error: {e}"}
        mode = (self.settings.preview_mode or "external").lower()
        if mode == "embedded":
            return {"ok": True, "mode": "embedded", "url": url, "title": title}
        return self._preview_launch_external(url, title)

    def _preview_launch_external(self, url: str, title: str) -> dict:
        exe, kind = self._find_preview_player()
        if not exe:
            return {"ok": False, "error":
                    "Preview: no player found. Install mpv (https://mpv.io) or "
                    "VLC (https://videolan.org), or set a player path in "
                    "Settings. Or switch preview to Embedded (built-in)."}
        wtitle = f"Preview — {title}"
        if kind == "mpv":
            cmd = [exe, "--profile=low-latency", "--force-window=yes",
                   "--keep-open=no", f"--title={wtitle}", url]
        elif kind == "vlc":
            cmd = [exe, "--no-video-title-show", "--network-caching=1500",
                   f"--meta-title={wtitle}", url]
        else:
            cmd = [exe, "-autoexit", "-window_title", wtitle, url]
        try:
            subprocess.Popen(cmd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            return {"ok": False, "error": f"Preview launch failed: {e}"}
        threading.Thread(target=self._focus_external_window, args=(wtitle,),
                         daemon=True).start()
        self._log_add(f"Preview: {kind} window opened for {title}.")
        return {"ok": True, "mode": "external", "player": kind, "title": title}

    @staticmethod
    def _focus_external_window(title: str, timeout: float = 6.0):
        try:
            user32 = ctypes.windll.user32
        except Exception:
            return
        deadline = time.time() + timeout
        hwnd = 0
        while time.time() < deadline:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.25)
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    # ── Browser / clipboard / folder helpers ──────────────────────────────────

    def launch_urls(self, items: list, target: str):
        opened = 0
        for url in items:
            try:
                if not target or target == "system":
                    webbrowser.open(url)
                else:
                    subprocess.Popen([target, url])
                opened += 1
            except Exception:
                try:
                    webbrowser.open(url)
                    opened += 1
                except Exception as e:
                    self._log_add(f"Browser open failed ({e}).", "warn")
            time.sleep(0.12)
        self._log_add(f"Opened {opened} model page(s) in browser")

    # ── System check ──────────────────────────────────────────────────────────

    def _module_ok(self, name: str) -> bool:
        try:
            import importlib
            importlib.import_module(name)
            return True
        except Exception:
            return False

    def _find_installed_dir(self, kind: str):
        exe = {"mpv": "mpv.exe", "vlc": "vlc.exe"}.get(kind)
        dirs = {
            "mpv": [r"%ProgramFiles%\MPV Player", r"%ProgramFiles%\mpv",
                    r"%ProgramFiles(x86)%\mpv", r"%LOCALAPPDATA%\Programs\mpv"],
            "vlc": [r"%ProgramFiles%\VideoLAN\VLC", r"%ProgramFiles(x86)%\VideoLAN\VLC"],
        }.get(kind, [])
        for d in dirs:
            d = os.path.expandvars(d)
            if exe and os.path.isfile(os.path.join(d, exe)):
                return d
        return None

    def _playwright_chromium_ok(self) -> bool:
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
        try:
            return os.path.isdir(base) and any(
                n.startswith("chromium-") for n in os.listdir(base))
        except Exception:
            return False

    def system_check(self) -> list:
        """Dependency rows for the Settings tab. Actions are serialized as
        {act, arg} and executed by Bridge.sys_fix."""
        out = []
        ff = (getattr(self.recorder, "ffmpeg_path", "") or "") or (shutil.which("ffmpeg") or "")
        out.append({"label": "ffmpeg (recording)", "state": "ok" if ff else "err",
                    "detail": ff or "NOT FOUND — recording won't work", "actions": []})
        fp = self._detect_player("ffplay")
        out.append({"label": "ffplay (external preview)", "state": "ok" if fp else "warn",
                    "detail": fp or "not found", "actions": []})
        wmpv = shutil.which("mpv")
        if wmpv:
            out.append({"label": "mpv (preview)", "state": "ok", "detail": wmpv,
                        "actions": []})
        else:
            loc = self._find_installed_dir("mpv")
            if loc:
                out.append({"label": "mpv (preview)", "state": "warn",
                            "detail": f"installed at {loc} but not on PATH",
                            "actions": [{"label": "Add to PATH",
                                         "act": "path", "arg": loc}]})
            else:
                out.append({"label": "mpv (preview)", "state": "warn",
                            "detail": "not installed (optional — VLC/ffplay cover preview)",
                            "actions": []})
        vlc_exe = self._detect_player("vlc")
        out.append({"label": "VLC (external preview)", "state": "ok" if vlc_exe else "warn",
                    "detail": vlc_exe or "not installed (optional)", "actions": []})
        out.append({"label": "Embedded preview (built-in player)", "state": "ok",
                    "detail": "hls.js in-app player — no install needed", "actions": []})
        td = self._module_ok("tdjson")
        out.append({"label": "tdjson (Telegram pipeline)", "state": "ok" if td else "warn",
                    "detail": "installed" if td else "missing",
                    "actions": [] if td else [{"label": "Install",
                                               "act": "pip", "arg": "tdjson"}]})
        pc = self._playwright_chromium_ok()
        out.append({"label": "Playwright Chromium (Stripchat fallback)",
                    "state": "ok" if pc else "warn",
                    "detail": "installed" if pc else "missing",
                    "actions": [] if pc else [{"label": "Install",
                                               "act": "chromium", "arg": ""}]})
        return out

    def fix_add_to_path(self, d: str):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            try:
                cur, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                cur = ""
            parts = [p for p in (cur or "").split(";") if p]
            if d not in parts:
                parts.append(d)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, None)
            winreg.CloseKey(key)
            if d not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + d
            self._log_add(f"Added to PATH: {d}", "success")
        except Exception as e:
            self._log_add(f"Could not modify PATH: {e}", "error")

    def bg_command(self, cmd, desc):
        self._log_add(f"{desc}… (one-time, needs internet)")

        def _do():
            ok, detail = False, ""
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = (r.returncode == 0)
                if not ok:
                    lines = (r.stderr or r.stdout or "").strip().splitlines()
                    detail = lines[-1] if lines else "failed"
            except Exception as e:
                detail = str(e)
            self._log_add(f"{desc}: {'done' if ok else 'failed — ' + detail}",
                          "success" if ok else "error")
        threading.Thread(target=_do, daemon=True, name="bg-cmd").start()

    # ── Shutdown / terminate ──────────────────────────────────────────────────

    def _active_recording_count(self) -> int:
        try:
            return sum(1 for cfg in self.recorder.models.values()
                       if getattr(cfg, "status", None) == ModelStatus.RECORDING)
        except Exception:
            return 0

    def _api_quit(self):
        if self._closing:
            return
        self._closing = True
        self._rec_pool.shutdown(wait=False, cancel_futures=True)
        self._stop_api_server()
        self._remove_tray()
        self._persist_models()

        def _shutdown():
            try:
                self.recorder.stop_monitor()
            except Exception:
                pass
            try:
                if self.pipeline:
                    self.pipeline.stop()
            except Exception:
                pass
            try:
                if self.window is not None:
                    self.window.destroy()
            except Exception:
                os._exit(0)
        threading.Thread(target=_shutdown, daemon=True, name="shutdown").start()

    def _force_terminate(self):
        try:
            subprocess.Popen(
                ["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            os._exit(1)

    # ── Local control API (port 5200) ─────────────────────────────────────────

    def _start_api_server(self):
        _ApiHandler._app = self
        try:
            self._api_server = HTTPServer(("127.0.0.1", _API_PORT), _ApiHandler)
            threading.Thread(target=self._api_server.serve_forever,
                             daemon=True, name="api-server").start()
        except OSError as e:
            self._api_server = None
            log.warning("API port %s busy (%s) — another Scr33nX is running.",
                        _API_PORT, e)
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    "You can only open one instance of this app.\n\n"
                    f"Another Scr33nX is already running (the control port "
                    f"{_API_PORT} is in use — check the system tray or "
                    f"taskbar).",
                    "Scr33nX is already running", 0x10)
            except Exception:
                pass
            os._exit(0)

    def _stop_api_server(self):
        srv = getattr(self, "_api_server", None)
        if srv is not None:
            try:
                threading.Thread(target=srv.shutdown, daemon=True).start()
            except Exception:
                pass
            self._api_server = None

    # ── Tray ──────────────────────────────────────────────────────────────────

    def _ensure_tray(self):
        if self._tray is not None or classic.WinTray is None:
            return
        tray = classic.WinTray(
            "Scr33nX",
            on_show=lambda: self.after(0, self._restore_from_tray),
            on_quit=lambda: self.after(0, self._api_quit),
            on_terminate=lambda: self.after(0, self._force_terminate))
        # WinTray only creates the icon once .add() runs (async, on its own
        # thread). Wait briefly for it to report ready/failed so we never hide
        # the window when there's no tray icon to bring it back.
        tray.add()
        ready = getattr(tray, "_ready", None)
        if ready is not None:
            ready.wait(timeout=2.0)
        if tray.failed():
            self._log_add("Tray icon could not be created.", "warn")
            try:
                tray.remove()
            except Exception:
                pass
            return
        self._tray = tray

    def _restore_from_tray(self):
        if self.window is not None:
            for op in ("show", "restore"):   # un-hide, then un-minimize
                try:
                    getattr(self.window, op)()
                except Exception:
                    pass
        self._remove_tray()

    def _remove_tray(self):
        if self._tray is not None:
            try:
                self._tray.remove()
            except Exception:
                pass
            self._tray = None

    def _on_minimized(self):
        if self.settings.minimize_to_tray and self.window is not None:
            self._ensure_tray()
            if self._tray is not None:
                try:
                    self.window.hide()
                except Exception:
                    pass

    # ── Background loops ──────────────────────────────────────────────────────

    def _meter_loop(self):
        bw_prev = ul_prev = None
        while True:
            try:
                total = cb_relay.bytes_downloaded()
            except Exception:
                total = 0
            now = time.monotonic()
            if bw_prev is not None:
                t0, b0 = bw_prev
                cur = (total - b0) * 8 / max(now - t0, 0.001) / 1_000_000
                self._bw_mbps = 0.6 * self._bw_mbps + 0.4 * cur
            bw_prev = (now, total)
            try:
                ul_total = self.pipeline.bytes_uploaded() if self.pipeline else 0
            except Exception:
                ul_total = 0
            if ul_prev is not None:
                t0, b0 = ul_prev
                cur = max(0.0, (ul_total - b0) * 8 / max(now - t0, 0.001) / 1_000_000)
                if cur <= 500.0:
                    self._ul_mbps = 0.6 * self._ul_mbps + 0.4 * cur
            ul_prev = (now, ul_total)
            time.sleep(1.0)

    def _size_loop(self):
        while True:
            for key, cfg in list(self.recorder.models.items()):
                session = getattr(cfg, "session", None)
                f = getattr(session, "current_file", None) if session else None
                if f:
                    try:
                        self._size_cache[key] = os.path.getsize(f)
                    except OSError:
                        pass
                else:
                    self._size_cache.pop(key, None)
            time.sleep(2.0)

    def _check_for_updates(self):
        import urllib.request
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            req = urllib.request.Request(
                url, headers={"Accept": "application/vnd.github+json",
                              "User-Agent": f"Scr33nX/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return
        latest = str(data.get("tag_name") or "").strip()
        cur = classic.StreamRecorderApp._parse_ver(APP_VERSION)
        new = classic.StreamRecorderApp._parse_ver(latest)
        if new and new > cur:
            self._update_latest = latest

    # ── Dashboard / snapshot ──────────────────────────────────────────────────

    def _dashboard_counts(self):
        tally = {s: [0, 0, 0, 0] for s, _l in _DASH_SITES}
        for k in list(self._rows):
            t = tally.get(k.split(":", 1)[0])
            if t is None:
                continue
            t[0] += 1
            cfg = self.recorder.models.get(k)
            st = cfg.status if cfg else None
            if st == ModelStatus.RECORDING:
                t[1] += 1
            elif st == ModelStatus.ONLINE:
                t[2] += 1
            else:
                t[3] += 1
        total = [0, 0, 0, 0]
        for c in tally.values():
            for i in range(4):
                total[i] += c[i]
        return tally, total

    def _api_dashboard(self) -> dict:
        tally, total = self._dashboard_counts()
        keys = ("total", "recording", "online", "offline")
        sites = {lbl: dict(zip(keys, tally[s])) for s, lbl in _DASH_SITES}
        return {"ok": True, "sites": sites, "all": dict(zip(keys, total))}

    def snapshot(self, log_after: int = 0, pipe_after: int = 0) -> dict:
        models = []
        for key in sorted(self._rows):
            site, name = key.split(":", 1)
            cfg = self.recorder.models.get(key)
            status = _STATUS_STR.get(cfg.status, "offline") if cfg else "offline"
            fname = ""
            size = ""
            session = cfg.session if cfg else None
            if session and getattr(session, "current_file", None):
                fname = os.path.basename(session.current_file)
                b = self._size_cache.get(key)
                if b:
                    size = _fmt_size(b)
            elif status == "error":
                fname = (self._last_detail.get(key) or "error")[:60]
            models.append({
                "key": key, "name": name, "site": site, "status": status,
                "file": fname, "size": size,
                "auto": self._auto_rec.get(key, False),
                "rank": self._get_rank(name, site),
                "saved": self._saved_key(name, site) in self._saved_data,
                "q": self._model_q.get(key, 0),
            })
        # Saved statuses: only non-offline models are sent; absence = offline.
        saved_status = {}
        for sid, d in list(self._saved_data.items()):
            key = f"{d['site']}:{d['name'].lower()}"
            cfg = self.recorder.models.get(key)
            if not cfg or cfg.status == ModelStatus.OFFLINE:
                continue
            st = _STATUS_STR.get(cfg.status, "offline")
            fname = ""
            size = ""
            session = cfg.session
            if session and getattr(session, "current_file", None):
                fname = os.path.basename(session.current_file)
                b = self._size_cache.get(key)
                if b:
                    size = _fmt_size(b)
            saved_status[sid] = [st, fname, size]
        with self._log_lock:
            log_new = [e for e in self._log_ring if e["i"] > int(log_after or 0)]
            pipe_new = [e for e in self._pipe_ring if e["i"] > int(pipe_after or 0)]
        pipe_running = bool(self.pipeline and self.pipeline.running)
        return {
            "version": APP_VERSION,
            "update": self._update_latest,
            "monitoring": {"recorder": self._monitoring_recorder,
                           "saved": self._monitoring_saved},
            "meters": {"down": round(self._bw_mbps, 1) if self._bw_mbps >= 0.05 else 0.0,
                       "up": round(self._ul_mbps, 1) if self._ul_mbps >= 0.05 else 0.0},
            "dash": self._api_dashboard(),
            "models": models,
            "saved_count": len(self._saved_data),
            "saved_version": self._saved_version,
            "saved_status": saved_status,
            "active_recordings": self._active_recording_count(),
            "low_disk": self.recorder.low_disk_blocked() is not None,
            "privacy": self.settings.privacy_mode_enabled,
            "log": log_new, "log_seq": self._log_seq,
            "pipe": {"running": pipe_running, "state": self._pipe_state,
                     "convert": self.settings.pipeline_do_convert,
                     "upload": self.settings.pipeline_do_upload,
                     "lines": [self._pipe_lines["convert"],
                               self._pipe_lines["upload1"],
                               self._pipe_lines["upload2"]],
                     "log": pipe_new, "log_seq": self._pipe_seq},
        }


class Bridge:
    """The js_api surface exposed to the page (window.pywebview.api)."""

    def __init__(self, core: WebCore):
        self._core = core

    # ── state ──
    def state(self, log_after=0, pipe_after=0):
        return self._core.snapshot(int(log_after or 0), int(pipe_after or 0))

    def saved_list(self):
        c = self._core
        items = [{"sid": sid, "name": d["name"], "site": d["site"],
                  "rank": c._get_rank(d["name"], d["site"])}
                 for sid, d in sorted(c._saved_data.items())]
        return {"version": c._saved_version, "items": items}

    # ── add / monitor ──
    def add_model(self, raw, site):
        name, site = parse_model_input(str(raw or ""), str(site or ""))
        if not name:
            return {"ok": False, "error": "Could not extract a username from the input."}
        key = f"{site}:{name}"
        if key in self._core._rows:
            return {"ok": False, "error": f"{name} ({site}) is already in the list."}
        self._core.after(0, lambda: self._core._add_to_recorder(name, site))
        return {"ok": True, "name": name, "site": site}

    def set_monitor(self, target, enabled):
        if target not in ("recorder", "saved"):
            return {"ok": False, "error": "bad target"}
        self._core.after(0, lambda: self._core._api_set_monitor(target, bool(enabled)))
        return {"ok": True}

    # ── recorder row actions (keys = ["site:name", ...]) ──
    @staticmethod
    def _split_keys(keys):
        out = []
        for k in list(keys or []):
            site, name = str(k).split(":", 1)
            out.append((name, site))
        return out

    def record(self, keys, start):
        c = self._core
        for name, site in self._split_keys(keys):
            if bool(start):
                c._launch_recording(name, site)
            else:
                threading.Thread(target=c.recorder.stop_recording,
                                 args=(name, site), daemon=True).start()
        return {"ok": True}

    def set_auto(self, key, enabled):
        key = str(key or "")
        if key not in self._core._rows:
            return {"ok": False, "error": "not in recorder"}
        self._core.after(0, lambda: self._core._set_auto(key, bool(enabled)))
        return {"ok": True}

    def toggle_auto(self, keys):
        c = self._core
        ks = [k for k in (keys or []) if k in c._rows]
        # Same semantics as classic bulk toggle: flip each row individually.
        def _do():
            for k in ks:
                c._auto_rec[k] = not c._auto_rec.get(k, False)
            c._persist_models()
        c.after(0, _do)
        return {"ok": True}

    def remove(self, keys):
        c = self._core
        skipped = 0
        def _do():
            nonlocal skipped
            for name, site in self._split_keys(keys):
                key = f"{site}:{name}"
                cfg = c.recorder.models.get(key)
                if cfg and cfg.status == ModelStatus.RECORDING:
                    skipped += 1
                    continue
                c._do_remove_from_recorder(name, site)
            if skipped:
                c._log_add(f"{skipped} recording model(s) not removed — stop "
                           "them first.", "warn")
        c.after(0, _do)
        return {"ok": True}

    def remove_offline(self):
        c = self._core
        def _do():
            removed = 0
            for key in list(c._rows):
                cfg = c.recorder.models.get(key)
                if cfg is None or cfg.status == ModelStatus.OFFLINE:
                    site, name = key.split(":", 1)
                    c._do_remove_from_recorder(name, site)
                    removed += 1
            c._log_add(f"Removed {removed} offline model(s).", "warn")
        c.after(0, _do)
        return {"ok": True}

    def offline_count(self):
        c = self._core
        n = 0
        for key in list(c._rows):
            cfg = c.recorder.models.get(key)
            if cfg is None or cfg.status == ModelStatus.OFFLINE:
                n += 1
        return {"count": n}

    def add_saved(self, keys):
        c = self._core
        def _do():
            for name, site in self._split_keys(keys):
                if c._saved_key(name, site) not in c._saved_data:
                    c._add_to_saved(name, site)
        c.after(0, _do)
        return {"ok": True}

    def stop_all(self):
        self._core.after(0, self._core._api_stop_all)
        return {"ok": True}

    def clear_recorder(self):
        self._core.after(0, self._core._do_clear_recorder)
        return {"ok": True}

    def set_rank(self, keys, rank, saved=False):
        items = []
        for k in list(keys or []):
            if saved:
                _, site, name = str(k).split(":", 2)
            else:
                site, name = str(k).split(":", 1)
            items.append((name, site))
        self._core.after(0, lambda: self._core._set_rank_many(items, int(rank)))
        return {"ok": True}

    def get_rank(self, name, site):
        return {"rank": self._core._get_rank(str(name), str(site))}

    def set_quality(self, keys, height):
        ks = [str(k) for k in (keys or [])]
        self._core.after(0, lambda: self._core._set_quality(ks, int(height or 0)))
        return {"ok": True}

    def quality_options(self):
        return {"options": [{"label": l, "height": h}
                            for l, h in QUALITY_OPTIONS.items()]}

    # ── clipboard / browser / folders ──
    def copy_urls(self, keys, saved=False, onetab=False):
        items = []
        for k in list(keys or []):
            if saved:
                _, site, name = str(k).split(":", 2)
            else:
                site, name = str(k).split(":", 1)
            url = _SITE_URLS.get(site, "https://{}/").format(name)
            items.append((name, site, url))
        if onetab:
            text = "\n".join(f"{url} | {name} ({site})" for name, site, url in items)
            msg = f"Copied {len(items)} model(s) as OneTab list"
        else:
            text = "\n".join(url for _n, _s, url in items)
            msg = f"Copied URL{'s' if len(items) > 1 else ''}: " + \
                  (items[0][2] if len(items) == 1 else f"{len(items)} models")
        ok = _set_clipboard(text)
        if ok:
            self._core._log_add(msg)
        return {"ok": ok}

    def browsers(self):
        return {"browsers": [{"name": n, "path": p} for n, p in _detect_browsers()],
                "preferred": self._core.settings.preferred_browser}

    def open_browser(self, keys, saved=False, target=None, remember=False):
        c = self._core
        items = []
        for k in list(keys or []):
            if saved:
                _, site, name = str(k).split(":", 2)
            else:
                site, name = str(k).split(":", 1)
            items.append(_SITE_URLS.get(site, "https://{}/").format(name))
        if not items:
            return {"ok": False}
        if target is None:
            target = c.settings.preferred_browser
            if not target:
                return {"ok": False, "choose": True}   # page shows the picker
        else:
            target = str(target)
            if remember:
                c.settings.preferred_browser = target
                save_settings(c.settings)
        threading.Thread(target=c.launch_urls, args=(items, target),
                         daemon=True, name="open-in-browser").start()
        return {"ok": True}

    def open_output_folder(self):
        try:
            os.makedirs(self._core.settings.output_dir, exist_ok=True)
            os.startfile(self._core.settings.output_dir)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── preview ──
    def preview(self, name, site):
        return self._core.preview_resolve(str(name), str(site))

    # ── saved models ──
    def saved_add(self, raw, site):
        name, site = parse_model_input(str(raw or ""), str(site or ""))
        if not name:
            return {"ok": False, "error": "Could not parse a username."}
        if self._core._saved_key(name, site) in self._core._saved_data:
            return {"ok": False, "error": f"{name} ({site}) is already in Saved Models."}
        self._core.after(0, lambda: self._core._add_to_saved(name, site))
        return {"ok": True, "name": name, "site": site}

    def saved_remove(self, sids):
        c = self._core
        def _do():
            for sid in list(sids or []):
                c._remove_saved(str(sid), persist=False)
            c._persist_models()
            c._log_add(f"Removed {len(sids)} model(s) from Saved Models", "warn")
        c.after(0, _do)
        return {"ok": True}

    def saved_to_recorder(self, sids):
        c = self._core
        def _do():
            added = 0
            for sid in list(sids or []):
                _, site, name = str(sid).split(":", 2)
                key = f"{site}:{name}"
                if key in c._rows:
                    continue
                c.recorder.add_model(name, site, "recorder", quiet=True)
                c._rows[key] = True
                c._auto_rec.setdefault(key, False)
                added += 1
            if added:
                c._persist_models()
                c._log_add(f"Added {added} model(s) to Recorder", "accent")
        c.after(0, _do)
        return {"ok": True}

    def saved_export(self):
        import webview
        c = self._core
        if not c._saved_data:
            return {"ok": False, "error": "Saved Models list is empty."}
        res = c.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename="saved_models.json",
            file_types=("JSON (*.json)",))
        path = res[0] if isinstance(res, (list, tuple)) and res else res
        if not path:
            return {"ok": False, "cancelled": True}
        merged: dict = {}
        existing = 0
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                for m in (prev.get("saved_models") if isinstance(prev, dict)
                          else []) or []:
                    if not isinstance(m, dict):
                        continue
                    n = (m.get("name") or "").strip()
                    s = (m.get("site") or "").strip()
                    if n and s:
                        merged[f"{s.lower()}:{n.lower()}"] = {
                            "name": n, "site": s,
                            "rank": int(m.get("rank", 0) or 0)}
                existing = len(merged)
            except Exception:
                pass
        updated = 0
        for d in c._saved_data.values():
            k = f"{d['site'].lower()}:{d['name'].lower()}"
            if k in merged:
                updated += 1
            merged[k] = {"name": d["name"], "site": d["site"],
                         "rank": c._get_rank(d["name"], d["site"])}
        items = list(merged.values())
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "saved_models": items}, f, indent=2)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        kept = existing - updated
        c._log_add(f"Exported {len(items)} saved model(s) → {path}", "success")
        msg = f"Wrote {len(items)} model(s) to:\n{path}"
        if existing:
            msg += f"\n\nMerged with existing file: {kept} kept, {updated} updated."
        return {"ok": True, "msg": msg}

    def saved_import(self):
        import webview
        c = self._core
        res = c.window.create_file_dialog(
            webview.OPEN_DIALOG, file_types=("JSON (*.json)", "All files (*.*)"))
        path = res[0] if isinstance(res, (list, tuple)) and res else res
        if not path:
            return {"ok": False, "cancelled": True}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return {"ok": False, "error": f"Could not read file:\n{e}"}
        items = data.get("saved_models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return {"ok": False, "error": "File is not a valid saved-models export."}
        added = skipped = invalid = ranked = 0
        for m in items:
            if not isinstance(m, dict):
                invalid += 1
                continue
            n = (m.get("name") or "").strip().lower()
            s = (m.get("site") or "").strip().lower()
            if not n or not s:
                invalid += 1
                continue
            rank = max(0, min(5, int(m.get("rank", 0) or 0)))
            sid = c._saved_key(n, s)
            rkey = c._rank_key(n, s)
            if sid in c._saved_data:
                if rank and c._get_rank(n, s) == 0:
                    c._ranks[rkey] = rank
                    ranked += 1
                else:
                    skipped += 1
                continue
            c._saved_data[sid] = {"name": n, "site": s}
            if rank:
                c._ranks[rkey] = rank
            if c._monitoring_saved:
                c.recorder.add_model(n, s, "saved", quiet=True)
            added += 1
        if added or ranked:
            c._saved_version += 1
            c._persist_models()
        c._log_add(f"Import: added {added}, ranked {ranked}, "
                   f"skipped {skipped} duplicate(s)" +
                   (f", {invalid} invalid" if invalid else ""),
                   "success" if (added or ranked) else "warn")
        msg = (f"Added: {added}\nRanks applied to existing: {ranked}\n"
               f"Skipped (already present): {skipped}")
        if invalid:
            msg += f"\nInvalid entries: {invalid}"
        return {"ok": True, "msg": msg}

    # ── pipeline ──
    def pipeline_toggle(self):
        self._core.after(0, self._core._toggle_pipeline)
        return {"ok": True}

    def pipeline_stage(self, convert=None, upload=None):
        c = None if convert is None else bool(convert)
        u = None if upload is None else bool(upload)
        self._core.after(0, lambda: self._core._api_set_pipeline_stage(c, u))
        return {"ok": True}

    def pipeline_save(self, fields):
        s = self._core.settings
        f = fields or {}
        s.telegram_api_id       = str(f.get("api_id", s.telegram_api_id)).strip()
        s.telegram_api_hash     = str(f.get("api_hash", s.telegram_api_hash)).strip()
        s.telegram_group_id     = str(f.get("group_id", s.telegram_group_id)).strip()
        s.telegram_topic_id     = str(f.get("topic_id", s.telegram_topic_id)).strip()
        s.pipeline_converted_dir = str(f.get("converted_dir", s.pipeline_converted_dir)).strip()
        s.telegram_session_dir  = str(f.get("session_dir", s.telegram_session_dir)).strip()
        save_pipeline_settings(s)
        self._core._pipe_log_add("Pipeline settings saved.", "accent")
        return {"ok": True}

    def pipeline_get(self):
        s = self._core.settings
        return {"api_id": s.telegram_api_id, "api_hash": s.telegram_api_hash,
                "group_id": s.telegram_group_id, "topic_id": s.telegram_topic_id,
                "converted_dir": s.pipeline_converted_dir,
                "session_dir": s.telegram_session_dir,
                "convert": s.pipeline_do_convert, "upload": s.pipeline_do_upload}

    def pipeline_reauth(self, confirmed=False):
        return self._core._pipeline_reauth(bool(confirmed))

    def pipe_prompt_answer(self, value):
        self._core._prompt_val = str(value or "")
        if self._core._prompt_evt:
            self._core._prompt_evt.set()
        return {"ok": True}

    # ── settings ──
    def get_settings(self):
        s = self._core.settings
        return {
            "output_dir": s.output_dir,
            "max_size_mb": s.max_size_mb if s.max_size_mb else "",
            "check_interval": s.check_interval,
            "max_quality": s.max_quality,
            "minimize_to_tray": s.minimize_to_tray,
            "notifications_enabled": s.notifications_enabled,
            "gap_warnings_enabled": s.gap_warnings_enabled,
            "auto_downgrade_enabled": s.auto_downgrade_enabled,
            "low_disk_guard_enabled": s.low_disk_guard_enabled,
            "playwright_fallback_enabled": s.playwright_fallback_enabled,
            "privacy_mode_enabled": s.privacy_mode_enabled,
            "preferred_browser": s.preferred_browser,
            "preview_mode": s.preview_mode,
            "preview_engine": s.preview_engine,
            "preview_player_path": s.preview_player_path,
            "quality_options": [{"label": l, "height": h}
                                for l, h in QUALITY_OPTIONS.items()],
        }

    def save_settings(self, payload):
        c = self._core
        s = c.settings
        p = payload or {}

        def _int(v, default=None):
            try:
                v = str(v).strip()
                return int(v) if v else default
            except (ValueError, TypeError):
                return default
        s.output_dir            = str(p.get("output_dir", s.output_dir)).strip() or s.output_dir
        s.max_size_mb           = _int(p.get("max_size_mb"), None)
        s.check_interval        = _int(p.get("check_interval"), 30) or 30
        s.max_quality           = _int(p.get("max_quality"), 0) or 0
        s.minimize_to_tray      = bool(p.get("minimize_to_tray"))
        s.notifications_enabled = bool(p.get("notifications_enabled"))
        s.gap_warnings_enabled  = bool(p.get("gap_warnings_enabled"))
        s.auto_downgrade_enabled = bool(p.get("auto_downgrade_enabled"))
        s.low_disk_guard_enabled = bool(p.get("low_disk_guard_enabled"))
        s.playwright_fallback_enabled = bool(p.get("playwright_fallback_enabled"))
        s.privacy_mode_enabled  = bool(p.get("privacy_mode_enabled"))
        s.preferred_browser     = str(p.get("preferred_browser", s.preferred_browser))
        s.preview_mode          = str(p.get("preview_mode", s.preview_mode))
        s.preview_engine        = str(p.get("preview_engine", s.preview_engine))
        s.preview_player_path   = str(p.get("preview_player_path", "")).strip()
        c.recorder.quality_global = s.max_quality
        c.recorder.auto_downgrade_enabled = s.auto_downgrade_enabled
        c.recorder.playwright_fallback_enabled = s.playwright_fallback_enabled
        c.recorder.low_disk_guard_enabled = s.low_disk_guard_enabled
        c.recorder.gap_warnings_enabled = s.gap_warnings_enabled
        c.recorder.output_dir = s.output_dir
        c.recorder.max_size_mb = s.max_size_mb
        c.recorder.check_interval = s.check_interval
        c._persist_models()
        try:
            os.makedirs(s.output_dir, exist_ok=True)
        except OSError:
            pass
        note = ""
        if _is := classic._is_cloud_synced(s.output_dir):
            c._log_add("⚠ Output folder is inside a cloud-synced directory "
                       "(OneDrive/Dropbox) — a local folder is strongly "
                       "recommended.", "warn")
        eng = (s.preview_engine or "auto").lower()
        if eng == "mpv" and not c._detect_player("mpv"):
            note = "mpv isn't installed — preview falls back to VLC/ffplay."
        elif eng == "vlc" and not c._detect_player("vlc"):
            note = "VLC isn't installed — preview falls back to another engine."
        c._log_add("Settings saved." + (f" ({note})" if note else ""), "success")
        return {"ok": True, "note": note}

    def pick_folder(self, initial=""):
        import webview
        res = self._core.window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=str(initial or ""))
        path = res[0] if isinstance(res, (list, tuple)) and res else res
        return {"path": path or ""}

    def system_check(self):
        return {"rows": self._core.system_check()}

    def sys_fix(self, act, arg=""):
        c = self._core
        act = str(act)
        if act == "path":
            c.after(0, lambda: c.fix_add_to_path(str(arg)))
        elif act == "pip":
            c.bg_command([sys.executable, "-m", "pip", "install", str(arg)],
                         f"Installing {arg}")
        elif act == "chromium":
            c.bg_command([sys.executable, "-m", "playwright", "install", "chromium"],
                         "Installing Playwright Chromium")
        else:
            return {"ok": False}
        return {"ok": True}

    # ── misc ──
    def active_recordings(self):
        return {"count": self._core._active_recording_count()}

    def terminate(self):
        self._core._force_terminate()
        return {"ok": True}

    def quit(self):
        self._core.after(0, self._core._api_quit)
        return {"ok": True}

    def open_url(self, url):
        url = str(url or "")
        if url.startswith("https://github.com/"):
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False}


def _apply_window_icon(title: str):
    """WM_SETICON with devil.ico — pywebview has no icon param on Windows."""
    try:
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = wintypes.HWND
        u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u32.LoadImageW.restype = wintypes.HANDLE
        u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                   wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                   wintypes.UINT]
        u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
        hwnd = u32.FindWindowW(None, title)
        if not hwnd:
            return
        ico = os.path.join(_SRC, "icons", "devil.ico")
        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        big = u32.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                             LR_LOADFROMFILE | LR_DEFAULTSIZE)
        small = u32.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if big:
            u32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
        if small:
            u32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
    except Exception:
        pass


def main():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Scr33nX.App")
    except Exception:
        pass
    try:
        import webview
    except ImportError:
        print("pywebview is required for the web UI:  pip install pywebview")
        sys.exit(1)

    core = WebCore()
    bridge = Bridge(core)
    window = webview.create_window(
        "Scr33nX", os.path.join(_SRC, "webui", "index.html"),
        js_api=bridge, width=1280, height=800, min_size=(980, 560),
        background_color="#0b0b0d")
    core.window = window

    def on_closing():
        if core._closing:
            return True
        n = core._active_recording_count()
        if n > 0:
            try:
                window.evaluate_js(f"UI.confirmQuit({n})")
            except Exception:
                return True
            return False
        core._api_quit()
        return False

    window.events.closing += on_closing
    try:
        window.events.minimized += core._on_minimized
    except Exception:
        pass
    window.events.shown += lambda: threading.Timer(
        0.4, _apply_window_icon, args=("Scr33nX",)).start()

    webview.start(gui="edgechromium", debug=("--debug" in sys.argv))
    if not core._closing:
        core._api_quit()
    time.sleep(0.5)
    os._exit(0)


if __name__ == "__main__":
    main()
