"""
app_web.py — Scr33nX web UI (redesign) — native window shell.

Hosts the SAME engine as app.py (StreamRecorder, settings persistence, the
port-5200 control API, tray, update check) but renders the interface as
HTML/CSS (src/webui/) inside a native pywebview window (Windows WebView2 —
no browser is involved). The classic Tk app stays untouched and launchable
while the redesign is built tab by tab.

Run with:  python src/app_web.py        (add --debug for devtools)
"""

import os
import sys
import json
import time
import queue
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
from app import _ApiHandler, APP_VERSION, GITHUB_REPO, _API_PORT
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

_DASH_SITES = [          # engine site key → short label (order = display order)
    ("chaturbate", "CB"),
    ("stripchat",  "SC"),
    ("camsoda",    "CS"),
    ("myfreecams", "MFC"),
]

_SITES = ("chaturbate", "stripchat", "camsoda", "myfreecams")


def parse_model_input(raw: str, fallback_site: str) -> tuple[str, str]:
    """Username or URL → (name, site). Same rules as the classic app:
    chaturbate.com/name, stripchat.com/name, camsoda.com/name,
    myfreecams.com/#name (hash fragment), else plain name + dropdown site."""
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
        self.pipeline = None            # Telegram pipeline arrives in Phase 5
        self.window = None              # pywebview window, set by main()
        self._tray = None
        self._closing = False

        self._rec_pool = ThreadPoolExecutor(max_workers=self._LAUNCH_POOL_SIZE,
                                            thread_name_prefix="rec-launch")
        self._launching: set[str] = set()
        self._launching_lock = threading.Lock()

        self._log_ring: deque = deque(maxlen=500)   # {"i","t","m","k"}
        self._log_seq = 0
        self._log_lock = threading.Lock()
        self._last_detail: dict[str, str] = {}      # key → last status detail
        self._size_cache: dict[str, int] = {}       # key → bytes (sweep thread)
        self._update_latest = ""                    # "vX.Y" when newer exists
        self._bw_mbps = 0.0
        self._ul_mbps = 0.0

        # Core task queue — the after() shim below feeds it, replacing the Tk
        # event loop the API handler used to schedule onto.
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
        self._start_api_server()   # also the single-instance gate
        if classic._is_cloud_synced(self.settings.output_dir):
            self._log_add("⚠ Output folder is inside a cloud-synced directory "
                          "(OneDrive/Dropbox) — sync uploads compete with "
                          "recording bandwidth and file locks can break "
                          "splitting. A local folder is strongly recommended.",
                          "warn")
        self._log_add(f"Scr33nX v{APP_VERSION} — web UI shell started "
                      f"({len(self._rows)} recorder model(s), "
                      f"{len(self._saved_data)} saved).", "accent")

    # ── Tk-compat scheduling shim ─────────────────────────────────────────────

    def after(self, delay_ms: int, fn):
        """API-handler compatible: run fn on the core task thread."""
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
        # Auto-record: monitor active + AUTO checked + model came online.
        # Same guard as the classic app: skip if a session is already running.
        if (status == ModelStatus.ONLINE and self._monitoring_recorder
                and self._auto_rec.get(key, False)):
            cfg = self.recorder.models.get(key)
            if not (cfg and cfg.session):
                site, name = key.split(":", 1)
                self._launch_recording(name, site)

    def _cb_log(self, msg: str):
        self._log_add(msg, "info", echo=False)   # recorder already file-logs

    def _cb_notif(self, title: str, msg: str):
        if self.settings.notifications_enabled:
            try:
                send_notification(title, msg)
            except Exception:
                pass

    def _launch_recording(self, name: str, site: str):
        """Throttled recording start (same pool+dedupe as the classic app)."""
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

    # ── Log ring (feeds the Activity Log tab) ─────────────────────────────────

    def _log_add(self, msg: str, kind: str = "info", echo: bool = True):
        with self._log_lock:
            self._log_seq += 1
            self._log_ring.append({"i": self._log_seq,
                                   "t": datetime.now().strftime("%H:%M:%S"),
                                   "m": str(msg), "k": kind})
        if echo:
            log.info("%s", msg)

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

    # ── Actions (shared by HTTP API and the JS bridge) ────────────────────────

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
            self._persist_models()
        return changed

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

    # Pipeline lands in Phase 5 — persist stage choices so nothing is lost,
    # but the worker itself only runs in the classic app for now.
    def _api_set_pipeline(self, enabled: bool):
        self._log_add("Pipeline is not available in the web UI yet (Phase 5) — "
                      "use the classic app.", "warn")

    def _api_set_pipeline_stage(self, convert=None, upload=None):
        if convert is not None:
            self.settings.pipeline_do_convert = bool(convert)
        if upload is not None:
            self.settings.pipeline_do_upload = bool(upload)
        save_pipeline_settings(self.settings)
        self._log_add("Pipeline stage flags saved (worker runs in the classic "
                      "app until Phase 5).", "warn")

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

    def _active_recording_count(self) -> int:
        try:
            return sum(1 for cfg in self.recorder.models.values()
                       if getattr(cfg, "status", None) == ModelStatus.RECORDING)
        except Exception:
            return 0

    # ── Shutdown / terminate ──────────────────────────────────────────────────

    def _api_quit(self):
        """Graceful shutdown, no dialogs (bot `close`, tray Exit, in-app quit)."""
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
                if self.window is not None:
                    self.window.destroy()
            except Exception:
                os._exit(0)
        threading.Thread(target=_shutdown, daemon=True, name="shutdown").start()

    def _force_terminate(self):
        """Hard-kill the whole process tree (ffmpeg, relay, Chromium) — Task
        Manager's End Task. The UI confirms first when recordings are active."""
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
                import ctypes
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
        # Callbacks fire on the tray thread — hop to the core task thread.
        tray = classic.WinTray(
            "Scr33nX",
            on_show=lambda: self.after(0, self._restore_from_tray),
            on_quit=lambda: self.after(0, self._api_quit),
            on_terminate=lambda: self.after(0, self._force_terminate))
        if tray.failed():
            self._log_add("Tray icon could not be created.", "warn")
            return
        self._tray = tray

    def _restore_from_tray(self):
        try:
            if self.window is not None:
                self.window.restore()
                self.window.show()
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
        """Ports the classic 1 s bandwidth tick (same smoothing/clamps)."""
        import cb_relay
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
                if cur <= 500.0:   # TDLib dedupe jumps aren't wire traffic
                    self._ul_mbps = 0.6 * self._ul_mbps + 0.4 * cur
            ul_prev = (now, ul_total)
            time.sleep(1.0)

    def _size_loop(self):
        """One worker polls file sizes (getsize can block on synced folders)."""
        while True:
            for key in list(self._rows):
                cfg = self.recorder.models.get(key)
                session = cfg.session if cfg else None
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

    # ── State snapshot for the web UI (pull model, 1 Hz) ──────────────────────

    def snapshot(self, log_after: int = 0) -> dict:
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
            })
        with self._log_lock:
            log_new = [e for e in self._log_ring if e["i"] > int(log_after or 0)]
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
            "active_recordings": self._active_recording_count(),
            "log": log_new,
            "log_seq": self._log_seq,
        }


class Bridge:
    """The js_api surface exposed to the page (window.pywebview.api)."""

    def __init__(self, core: WebCore):
        self._core = core

    def state(self, log_after=0):
        return self._core.snapshot(int(log_after or 0))

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

    def set_auto(self, key, enabled):
        key = str(key or "")
        if key not in self._core._rows:
            return {"ok": False, "error": "not in recorder"}
        self._core.after(0, lambda: self._core._set_auto(key, bool(enabled)))
        return {"ok": True}

    def terminate(self):
        """Hard kill. The page shows the confirm first when recording."""
        self._core._force_terminate()
        return {"ok": True}

    def quit(self):
        self._core.after(0, self._core._api_quit)
        return {"ok": True}

    def open_url(self, url):
        url = str(url or "")
        if url.startswith("https://github.com/"):   # releases page only
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False}


def _apply_window_icon(title: str):
    """WM_SETICON with devil.ico — pywebview has no icon param on Windows.
    Same 64-bit-safe ctypes declarations as the classic app."""
    try:
        import ctypes
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
        import ctypes
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
        """X button: quit like the classic app, but confirm first (in-page)
        when a recording is active."""
        if core._closing:
            return True
        n = core._active_recording_count()
        if n > 0:
            try:
                window.evaluate_js(f"UI.confirmQuit({n})")
            except Exception:
                return True
            return False          # page shows the dialog; quit goes via api
        core._api_quit()
        return False              # _api_quit destroys the window itself

    window.events.closing += on_closing
    try:
        window.events.minimized += core._on_minimized
    except Exception:
        pass
    window.events.shown += lambda: threading.Timer(
        0.4, _apply_window_icon, args=("Scr33nX",)).start()

    webview.start(gui="edgechromium", debug=("--debug" in sys.argv))
    # webview.start returns when the window is gone — make sure we exit even
    # if shutdown was initiated from the window's X handler.
    if not core._closing:
        core._api_quit()
    time.sleep(0.5)
    os._exit(0)


if __name__ == "__main__":
    main()
