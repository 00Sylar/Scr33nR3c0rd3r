"""
app.py — Scr33nX V1.0 — GUI
"""

APP_VERSION = "1.0"

import os
import sys
import json
import time
import math
import random
import threading
import tkinter as tk
from collections import deque
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from recorder import StreamRecorder, ModelStatus
from settings import AppSettings, load_settings, save_settings, save_pipeline_settings
from notifier import send_notification

# ── File logging ──────────────────────────────────────────────────────────────
# pythonw has no console (stderr is discarded), so without this any background
# thread error vanishes. Everything recorder.py / cb_relay.py logs — including
# monitor-crash tracebacks — lands in %LOCALAPPDATA%\Scr33nX\streamrecorder.log,
# with the thread name. Falls back to the home directory when unavailable.
import logging
import faulthandler
from logging.handlers import RotatingFileHandler

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Logs live under %LOCALAPPDATA%\Scr33nX, NOT next to app.py: the app dir is
# often cloud-synced (OneDrive), where sync file locks break the rotating
# handler's rollover rename and every log write causes sync churn.
_LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA")
                        or os.path.expanduser("~"), "Scr33nX")
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
except OSError:
    _LOG_DIR = os.path.expanduser("~")
LOG_FILE = os.path.join(_LOG_DIR, "streamrecorder.log")
try:
    _handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3,
                                   encoding="utf-8")
except OSError:
    LOG_FILE = os.path.join(os.path.expanduser("~"), "streamrecorder.log")
    _handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3,
                                   encoding="utf-8")
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(threadName)-16s %(levelname)-7s %(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)
CRASH_FILE = os.path.join(os.path.dirname(LOG_FILE), "streamrecorder_crash.log")
try:
    # faulthandler appends forever — start fresh once it grows past 1 MB
    _crash_mode = "a"
    try:
        if os.path.getsize(CRASH_FILE) > 1_000_000:
            _crash_mode = "w"
    except OSError:
        pass
    _crash_f = open(CRASH_FILE, _crash_mode)
    faulthandler.enable(_crash_f)
except OSError:
    pass


def _is_cloud_synced(path: str) -> bool:
    """True when `path` sits inside a OneDrive/Dropbox/Google Drive folder —
    cloud sync competes with recording bandwidth and its file locks interfere
    with file splitting and pipeline renames."""
    try:
        p = os.path.realpath(path).lower()
    except OSError:
        return False
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env)
        if root:
            try:
                if p.startswith(os.path.realpath(root).lower().rstrip("\\") + "\\"):
                    return True
            except OSError:
                pass
    return ("\\onedrive" in p or "\\dropbox" in p
            or "\\google drive" in p or "\\googledrive" in p)

if sys.platform == "win32":
    from tray_win import WinTray
else:
    WinTray = None  # type: ignore

# ── Stream quality caps ───────────────────────────────────────────────────────
# Display label ↔ max variant height (px); 0 = no cap (highest available).
QUALITY_OPTIONS = {
    "Unlimited (highest)": 0,
    "1080p": 1080,
    "720p":  720,
    "480p":  480,
}


def _quality_label(height: int) -> str:
    for lbl, h in QUALITY_OPTIONS.items():
        if h == height:
            return lbl
    return f"{height}p" if height else "Unlimited (highest)"

# ── Palette ───────────────────────────────────────────────────────────────────
# Elegant black & red — minimalist. Red is the single signature accent;
# supporting colors are muted so red and near-black carry the design.
BG      = "#0a0a0b"   # deep near-black (canvas)
BG2     = "#101011"   # header / elevated surfaces
BG3     = "#17171a"   # inputs, rows, cards
BORDER  = "#272729"   # hairline borders
ACCENT  = "#ff2b3d"   # signature red (brand, recording, primary actions)
ACCENT2 = "#c81f2e"   # deep red (hover / pressed)
GREEN   = "#3ecf8e"   # refined emerald (online / go)
RED     = "#ff2b3d"   # destructive — same red family as accent
ORANGE  = "#ff8a3d"   # warm amber (private / warnings)
YELLOW  = "#ffc14d"   # soft gold (checking)
TEXT    = "#f2f2f4"   # near-white
TEXT2   = "#8a8a90"   # muted grey
TEXT3   = "#56565c"   # dim grey
MONO    = ("Consolas", 10)
UI      = ("Segoe UI", 10)

PRIVACY_IDLE_SECONDS = 3   # idle time before privacy mode covers the window

STATUS_COLORS = {
    ModelStatus.OFFLINE:   (TEXT3,  "●  OFFLINE"),
    ModelStatus.ONLINE:    (GREEN,  "●  ONLINE"),
    ModelStatus.RECORDING: (ACCENT, "⬤  RECORDING"),
    ModelStatus.ERROR:     (RED,    "✖  ERROR"),
    ModelStatus.CHECKING:  (YELLOW, "◌  CHECKING..."),
    ModelStatus.PRIVATE:   (YELLOW, "🔒  PRIVATE / TICKET"),
}

# Reverse map: status-cell label text → ModelStatus (drives the status filter)
STATUS_BY_LABEL = {label: st for st, (_c, label) in STATUS_COLORS.items()}

# Status tag names used in the Treeview
STATUS_TAGS = {
    ModelStatus.OFFLINE:   "s_offline",
    ModelStatus.ONLINE:    "s_online",
    ModelStatus.RECORDING: "s_recording",
    ModelStatus.ERROR:     "s_error",
    ModelStatus.CHECKING:  "s_checking",
    ModelStatus.PRIVATE:   "s_private",
}


# ── Local API server (browser-extension bridge) ───────────────────────────────

_API_PORT = 5200  # v2 (TEST) uses 5200 to coexist with v1's 5199


class _ApiHandler(BaseHTTPRequestHandler):
    _app: "StreamRecorderApp" = None  # set after App() is built

    def do_OPTIONS(self):
        self._cors(200)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/dashboard":
            # Aggregate per-site + totals snapshot (read-only — safe off the
            # Tk thread, same as the /status reads below).
            self._json(self._app._api_dashboard())
            return
        if parsed.path != "/status":
            self._json({"error": "not found"}, 404)
            return
        qs   = parse_qs(parsed.query)
        name = qs.get("name", [""])[0].strip().lower()
        site = qs.get("site", [""])[0].strip().lower()
        if not name or not site:
            self._json({"error": "missing name or site"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        sid = f"saved:{key}"
        cfg = app.recorder.models.get(key)
        status_str = None
        if cfg:
            status_str = {
                ModelStatus.OFFLINE:   "offline",
                ModelStatus.ONLINE:    "online",
                ModelStatus.RECORDING: "recording",
                ModelStatus.CHECKING:  "checking",
                ModelStatus.ERROR:     "error",
                ModelStatus.PRIVATE:   "private",
            }.get(cfg.status, "offline")
        self._json({
            "in_recorder": key in app._rows,
            "in_saved":    sid in app._saved_data,
            "status":      status_str,
            "auto":        app._auto_rec.get(key, False),
            "rank":        app._get_rank(name, site),
        })

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/add":
            self._handle_add()
        elif parsed.path == "/record":
            self._handle_record()
        elif parsed.path == "/remove":
            self._handle_remove()
        elif parsed.path == "/auto":
            self._handle_auto()
        elif parsed.path == "/rank":
            self._handle_rank()
        elif parsed.path == "/stop_all":
            self._handle_stop_all()
        elif parsed.path == "/clear":
            self._handle_clear()
        elif parsed.path == "/monitor":
            self._handle_monitor()
        elif parsed.path == "/pipeline":
            self._handle_pipeline()
        elif parsed.path == "/quit":
            self._handle_quit()
        else:
            self._json({"error": "not found"}, 404)

    def _handle_add(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name   = body.get("name",   "").strip().lower()
        site   = body.get("site",   "").strip().lower()
        target = body.get("target", "recorder")
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        if site not in ("chaturbate", "stripchat", "camsoda", "myfreecams"):
            self._json({"ok": False, "error": f"unsupported site: {site}"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        sid = f"saved:{key}"
        # Pre-check so we return a useful error before scheduling
        if target == "saved" and sid in app._saved_data:
            self._json({"ok": False, "error": "Already in Saved Models"})
            return
        if target != "saved" and key in app._rows:
            self._json({"ok": False, "error": "Already in Recorder"})
            return
        if target == "saved":
            app.after(0, lambda n=name, s=site: app._add_to_saved(n, s))
        else:
            app.after(0, lambda n=name, s=site: app._add_to_recorder(n, s))
        self._json({"ok": True})

    def _handle_record(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name   = body.get("name",   "").strip().lower()
        site   = body.get("site",   "").strip().lower()
        action = body.get("action", "").strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        if action not in ("start", "stop"):
            self._json({"ok": False, "error": "action must be 'start' or 'stop'"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        if key not in app._rows:
            self._json({"ok": False, "error": "Model is not in the Recorder list"})
            return

        # Run the network/ffmpeg work off the HTTP handler thread so the
        # response returns quickly.
        def _run():
            try:
                if action == "start":
                    app.recorder.start_recording(name, site)
                else:
                    app.recorder.stop_recording(name, site)
            except Exception as e:
                app.after(0, lambda: app._log_add(
                    f"[api /record {action}] {name}/{site}: {e}", "warn"))

        threading.Thread(target=_run, daemon=True,
                         name=f"api-{action}-{name}").start()
        self._json({"ok": True})

    def _handle_remove(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name   = body.get("name",   "").strip().lower()
        site   = body.get("site",   "").strip().lower()
        target = body.get("target", "recorder").strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        app = self._app
        if target == "saved":
            sid = f"saved:{site}:{name}"
            if sid not in app._saved_data:
                self._json({"ok": False, "error": "Model is not in Saved Models"})
                return
            app.after(0, lambda s=sid: app._remove_saved(s))
            self._json({"ok": True})
            return
        if target != "recorder":
            self._json({"ok": False, "error": f"unsupported target: {target}"}, 400)
            return
        key = f"{site}:{name}"
        if key not in app._rows:
            self._json({"ok": False, "error": "Model is not in the Recorder list"})
            return
        cfg = app.recorder.models.get(key)
        if cfg and cfg.status == ModelStatus.RECORDING:
            self._json({"ok": False,
                        "error": "Stop the recording before removing"})
            return
        app.after(0, lambda n=name, s=site: app._do_remove_from_recorder(n, s))
        self._json({"ok": True})

    def _handle_auto(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name    = body.get("name", "").strip().lower()
        site    = body.get("site", "").strip().lower()
        enabled = bool(body.get("enabled", False))
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        if key not in app._rows:
            self._json({"ok": False, "error": "Model is not in the Recorder list"})
            return
        app.after(0, lambda: app._set_auto(key, enabled))
        self._json({"ok": True, "auto": enabled})

    def _handle_rank(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name = body.get("name", "").strip().lower()
        site = body.get("site", "").strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        try:
            rank = int(body.get("rank", 0))
        except (TypeError, ValueError):
            self._json({"ok": False, "error": "rank must be an integer 0-5"}, 400)
            return
        if not 0 <= rank <= 5:
            self._json({"ok": False, "error": "rank must be 0-5"}, 400)
            return
        app = self._app
        # Rank is keyed by model identity, so it works whether or not the model
        # is in a list. Setting it on a brand-new model just records the rank.
        app.after(0, lambda n=name, s=site, r=rank:
                  app._set_rank_many([(n, s)], r))
        self._json({"ok": True, "rank": rank})

    def _handle_stop_all(self):
        """Force-stop every active download and clear AUTO (no confirm dialog)."""
        self._app.after(0, self._app._api_stop_all)
        self._json({"ok": True})

    def _handle_clear(self):
        """Clean-slate the Recorder (no confirm dialog): stop monitor + all
        downloads, clear AUTO, remove every model. Saved Models untouched."""
        self._app.after(0, lambda: self._app._do_clear_recorder(via_api=True))
        self._json({"ok": True})

    def _handle_monitor(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        target  = body.get("target", "").strip().lower()
        enabled = bool(body.get("enabled", False))
        if target not in ("recorder", "saved"):
            self._json({"ok": False,
                        "error": "target must be 'recorder' or 'saved'"}, 400)
            return
        app = self._app
        app.after(0, lambda t=target, e=enabled: app._api_set_monitor(t, e))
        self._json({"ok": True, "target": target, "enabled": enabled})

    def _handle_pipeline(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        enabled = bool(body.get("enabled", False))
        app = self._app
        app.after(0, lambda e=enabled: app._api_set_pipeline(e))
        self._json({"ok": True, "enabled": enabled})

    def _handle_quit(self):
        # Reply first, THEN schedule the shutdown (which stops this very
        # server) so the client still receives the response.
        self._json({"ok": True})
        self._app.after(0, self._app._api_quit)

    def _cors(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self._cors(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence default access logs


# ─────────────────────────────────────────────────────────────────────────────

class StreamRecorderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Scr33nX")
        self._set_app_icon()
        self.geometry("1060x700")
        self.minsize(900, 520)
        self.configure(bg=BG)

        self.settings = load_settings()
        self.recorder = StreamRecorder()
        self.recorder.output_dir     = self.settings.output_dir
        self.recorder.max_size_mb    = self.settings.max_size_mb
        self.recorder.check_interval = self.settings.check_interval
        self.recorder.on_status_change = self._cb_status
        self.recorder.on_log           = self._cb_log
        self.recorder.on_notification  = self._cb_notif
        self.recorder.gap_warnings_enabled = self.settings.gap_warnings_enabled

        self._rows: dict[str, bool] = {}          # key → exists flag
        self._saved_rows: dict[str, bool] = {}    # saved-only models (view only)
        self._auto_rec: dict[str, bool] = {}      # key → auto-rec state (recorder tab)
        self._model_q: dict[str, int] = {}        # key → per-model quality cap (height px, 0 = default)
        # The recorder answers the relay's per-stream quality queries; the app
        # just feeds it the global cap and shares the per-model override dict.
        self.recorder.quality_global = self.settings.max_quality
        self.recorder.quality_overrides = self._model_q
        self.recorder.auto_downgrade_enabled = self.settings.auto_downgrade_enabled
        self.recorder.playwright_fallback_enabled = self.settings.playwright_fallback_enabled
        self._monitoring_recorder = False
        self._monitoring_saved    = False
        self._tray: Optional[WinTray] = None
        self._hiding_to_tray = False
        # Tray callbacks fire on the tray thread — they set these events,
        # which a Tk after-loop polls (never call Tk from the tray thread).
        self._tray_show_evt = threading.Event()
        self._tray_quit_evt = threading.Event()
        self._tray_poll_id: Optional[str] = None
        # Worker-thread log lines arrive at a high rate when many recordings
        # struggle (ffmpeg stderr + relay warnings). Queue them and insert in
        # one batch per 250 ms tick — one Tk event per LINE flooded the event
        # loop and made the privacy starfield (and the whole UI) stutter.
        # MUST exist before any add_model/_restore_models call: the recorder's
        # on_log callback appends here.
        self._log_queue: deque = deque()
        # Status callbacks mark stats dirty; one 500 ms tick recomputes them
        # instead of a full row scan per status event.
        self._stats_dirty = False
        # File sizes are polled by ONE worker thread (os.path.getsize off the
        # Tk thread — it can block on cloud-synced folders) and applied in a
        # single batched UI update; replaces the old per-model after() chains.
        self._size_cache: dict[str, str] = {}
        # Saved Models is lazy: _saved_data (sid → {name, site}) is the source
        # of truth, loaded from settings at startup. Treeview rows are built on
        # the first tab visit; engine registration happens at scanner start.
        # A 1500+ watchlist otherwise costs startup time, Tk memory, and makes
        # every engine scan iterate the full list even when the tab is unused.
        self._saved_data: dict[str, dict] = {}
        # Per-model 0-5 star rank, keyed by "site:name" (identity, not list) so
        # the same model shows the same rank in the Recorder tab, the Saved tab
        # and the browser extension. Persisted in settings.ranks.
        self._ranks: dict[str, int] = {
            k: int(v) for k, v in (getattr(self.settings, "ranks", None) or {}).items()
            if v
        }
        self._saved_built = False
        self._filter_jobs: dict[str, Optional[str]] = {"rec": None, "saved": None}
        # Row checkboxes (☐/☑ glyph in the name column): an explicit working
        # set per tab. When any row is checked, bulk actions operate on the
        # checked set instead of the click-selection.
        self._checked: set[str] = set()
        self._saved_checked: set[str] = set()
        # Press-row per tree, drives drag-selection (press a row, drag down →
        # the whole range gets selected, like a rubber band over a list)
        self._drag_anchor: dict[str, Optional[str]] = {"rec": None, "saved": None}
        self._build_styles()
        self._build_ui()
        self._restore_models()
        self._start_api_server()
        self._privacy_init()
        if _is_cloud_synced(self.settings.output_dir):
            self._log_add("⚠ Output folder is inside a cloud-synced directory "
                          "(OneDrive/Dropbox) — sync uploads compete with "
                          "recording bandwidth and file locks can break "
                          "splitting. A local folder is strongly recommended.",
                          "warn")
        self._bw_prev: Optional[tuple] = None   # (monotonic_ts, bytes_total)
        self._bw_mbps = 0.0
        self._ul_prev: Optional[tuple] = None   # (monotonic_ts, bytes_uploaded)
        self._ul_mbps = 0.0
        threading.Thread(target=self._size_sweep_loop, daemon=True,
                         name="size-sweep").start()
        self.after(250, self._drain_logs)
        self.after(500, self._stats_tick)
        self.after(1000, self._bw_tick)
        self.after(5000, self._sync_monitor_buttons)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if WinTray is not None:
            self.bind("<Unmap>", self._on_window_unmap)

    # ── App icon ──────────────────────────────────────────────────────────────

    def _set_app_icon(self):
        base = os.path.dirname(os.path.abspath(__file__))
        self._hdr_icon = None
        # Own AppUserModelID so the taskbar shows our window icon
        # instead of grouping under python.exe's rocket icon.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Scr33nX.App")
        except Exception:
            pass
        try:
            self.iconbitmap(os.path.join(base, "icons", "devil.ico"))
        except Exception:
            pass
        try:
            png = tk.PhotoImage(file=os.path.join(base, "icons", "devil.png"))
            self.iconphoto(True, png)
            # 512px source → ~28px header logo
            self._hdr_icon = png.subsample(18, 18)
        except Exception:
            pass

    # ── Styles ────────────────────────────────────────────────────────────────

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT,
                    fieldbackground=BG3, font=UI)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TEXT, font=UI)

        for name, bg, fg, abg in [
            ("Accent.TButton", ACCENT, BG,    ACCENT2),
            ("Green.TButton",  GREEN,  BG,    "#32a877"),
            ("Red.TButton",    RED,    TEXT,  ACCENT2),
            ("Ghost.TButton",  BG3,    TEXT,  BORDER),
            ("Flat.TButton",   BG2,    TEXT2, BG3),
        ]:
            s.configure(name, background=bg, foreground=fg,
                        font=("Segoe UI Semibold", 10),
                        padding=(10, 5), relief="flat", borderwidth=0)
            s.map(name, background=[("active", abg), ("pressed", abg)],
                  relief=[("pressed", "flat")])

        s.configure("TEntry", fieldbackground=BG3, foreground=TEXT,
                    insertcolor=ACCENT, relief="flat", padding=(8, 6))
        s.configure("TCombobox", fieldbackground=BG3, background=BG3,
                    foreground=TEXT, arrowcolor=TEXT2, relief="flat", padding=(8, 5))
        s.map("TCombobox", fieldbackground=[("readonly", BG3)],
              selectbackground=[("readonly", BG3)],
              selectforeground=[("readonly", TEXT)])
        s.configure("Vertical.TScrollbar", background=BG3, troughcolor=BG2,
                    relief="flat", arrowcolor=TEXT3, borderwidth=0)
        s.configure("TMenubutton", background=BG3, foreground=TEXT,
                    arrowcolor=TEXT2, relief="flat", padding=(8, 4),
                    font=("Segoe UI", 9))
        s.map("TMenubutton", background=[("active", BORDER)])
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=TEXT2,
                    font=("Segoe UI Semibold", 10), padding=(16, 8), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", BG3)],
              foreground=[("selected", ACCENT)])

        # ── Treeview dark style ──
        s.configure("Dark.Treeview",
                    background=BG2, foreground=TEXT, fieldbackground=BG2,
                    font=("Segoe UI", 10), rowheight=38,
                    borderwidth=0, relief="flat")
        s.configure("Dark.Treeview.Heading",
                    background=BG3, foreground=TEXT3,
                    font=("Segoe UI Semibold", 9),
                    borderwidth=0, relief="flat", padding=(8, 6))
        s.map("Dark.Treeview.Heading",
              background=[("active", BORDER)])
        s.map("Dark.Treeview",
              background=[("selected", BG3)],
              foreground=[("selected", ACCENT)])

    # ── UI Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG2, height=54)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        if self._hdr_icon is not None:
            tk.Label(hdr, image=self._hdr_icon, bg=BG2
                     ).pack(side="left", padx=(16, 6))
        else:
            tk.Label(hdr, text="⬤", fg=ACCENT, bg=BG2,
                     font=("Segoe UI", 17)).pack(side="left", padx=(16,4))
        tk.Label(hdr, text="Scr33n", fg=TEXT, bg=BG2,
                 font=("Segoe UI Black", 15)).pack(side="left")
        tk.Label(hdr, text="X", fg=ACCENT, bg=BG2,
                 font=("Segoe UI Black", 15)).pack(side="left", padx=(1,0))
        self._lbl_hdr_status = tk.Label(hdr, text="● IDLE", fg=TEXT3, bg=BG2,
                                         font=("Segoe UI Semibold", 10))
        self._lbl_hdr_status.pack(side="right", padx=12)
        # Live bandwidth meter: ↓ download of all active recordings,
        # ↑ upload of the pipeline pushing to Telegram.
        self._lbl_bw_up = tk.Label(hdr, text="↑ 0.0 Mbps", fg=TEXT3, bg=BG2,
                                   font=("Segoe UI Semibold", 10))
        self._lbl_bw_up.pack(side="right", padx=(0, 4))
        self._lbl_bw = tk.Label(hdr, text="↓ 0.0 Mbps", fg=TEXT3, bg=BG2,
                                font=("Segoe UI Semibold", 10))
        self._lbl_bw.pack(side="right", padx=(0, 10))

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

        # Body
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # Left panel
        left = tk.Frame(body, bg=BG2, width=258)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_left(left)

        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")

        # Right notebook
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        self._build_right(right)

    def _build_left(self, p):
        def label(text):
            tk.Label(p, text=text, fg=TEXT3, bg=BG2,
                     font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=16, pady=(14,3))

        label("ADD MODEL")
        tk.Label(p, text="Username or Link", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._e_name = ttk.Entry(p)
        self._e_name.pack(fill="x", padx=16, pady=(2,8))
        self._e_name.bind("<Return>", lambda e: self._add_model())
        # Auto-detect site when pasting a URL
        self._e_name.bind("<KeyRelease>", lambda e: self._auto_detect_site())

        tk.Label(p, text="Site", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._c_site = ttk.Combobox(p,
                                     values=["chaturbate","stripchat","camsoda","myfreecams"],
                                     state="readonly")
        self._c_site.set("chaturbate")
        self._c_site.pack(fill="x", padx=16, pady=(2,12))

        ttk.Button(p, text="＋  Add Model", style="Accent.TButton",
                   command=self._add_model).pack(fill="x", padx=16, pady=(0,14))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=12)
        label("SETTINGS")

        tk.Label(p, text="Output Folder", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        row = tk.Frame(p, bg=BG2)
        row.pack(fill="x", padx=16, pady=(2,8))
        self._e_folder = ttk.Entry(row)
        self._e_folder.pack(side="left", fill="x", expand=True)
        self._e_folder.insert(0, self.settings.output_dir)
        ttk.Button(row, text="…", style="Ghost.TButton",
                   command=self._pick_folder, width=3).pack(side="left", padx=(4,0))

        tk.Label(p, text="Max File Size (MB)", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        row2 = tk.Frame(p, bg=BG2)
        row2.pack(fill="x", padx=16, pady=(2,8))
        self._v_maxsize = tk.StringVar(
            value=str(self.settings.max_size_mb) if self.settings.max_size_mb else "")
        ttk.Entry(row2, textvariable=self._v_maxsize).pack(side="left", fill="x", expand=True)
        tk.Label(row2, text="MB", fg=TEXT3, bg=BG2, font=UI).pack(side="left", padx=(6,0))

        tk.Label(p, text="Check Interval (sec)", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._v_interval = tk.StringVar(value=str(self.settings.check_interval))
        ttk.Entry(p, textvariable=self._v_interval).pack(fill="x", padx=16, pady=(2,8))

        tk.Label(p, text="Max Quality (all models)", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._v_quality = tk.StringVar(value=_quality_label(self.settings.max_quality))
        ttk.Combobox(p, textvariable=self._v_quality, state="readonly",
                     values=list(QUALITY_OPTIONS)).pack(fill="x", padx=16, pady=(2,8))

        self._v_tray = tk.BooleanVar(value=self.settings.minimize_to_tray)
        tk.Checkbutton(p, text="⤵ Minimize to SysTray", variable=self._v_tray,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,4))

        self._v_notif = tk.BooleanVar(value=self.settings.notifications_enabled)
        tk.Checkbutton(p, text="🔔 Notifications", variable=self._v_notif,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_gapwarn = tk.BooleanVar(value=self.settings.gap_warnings_enabled)
        tk.Checkbutton(p, text="⚠ Dropped-Segment Warnings", variable=self._v_gapwarn,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_autodown = tk.BooleanVar(value=self.settings.auto_downgrade_enabled)
        tk.Checkbutton(p, text="⬇ Auto-Downgrade Quality",
                       variable=self._v_autodown,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_pwfallback = tk.BooleanVar(value=self.settings.playwright_fallback_enabled)
        tk.Checkbutton(p, text="🎭 Stripchat Browser Fallback",
                       variable=self._v_pwfallback,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_privacy = tk.BooleanVar(value=self.settings.privacy_mode_enabled)
        tk.Checkbutton(p, text="🔒 Privacy Mode", variable=self._v_privacy,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,4))

        ttk.Button(p, text="💾  Save Settings", style="Flat.TButton",
                   command=self._save_settings).pack(fill="x", padx=16, pady=(0,0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(16,0))
        self._build_stats_panel(p)

    def _build_right(self, p):
        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True)
        self._nb = nb

        # Recorder tab
        tab_m = ttk.Frame(nb)
        nb.add(tab_m, text="  Recorder  ")
        self._build_models_tab(tab_m)

        # Saved Models tab
        tab_s = ttk.Frame(nb)
        nb.add(tab_s, text="  Saved Models  ")
        self._build_saved_tab(tab_s)
        self._tab_saved = tab_s
        # Saved rows are built lazily on the first visit to the tab
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Output / Upload tab (Pipeline integration)
        tab_o = ttk.Frame(nb)
        nb.add(tab_o, text="  Output / Upload  ")
        self._build_output_tab(tab_o)

        # Log tab
        tab_l = ttk.Frame(nb)
        nb.add(tab_l, text="  Activity Log  ")
        self._build_log_tab(tab_l)

    # ── Models tab (Treeview — single native widget, no flicker) ─────────────

    def _build_models_tab(self, p):
        # ── Action bar ──
        bar = tk.Frame(p, bg=BG2, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._lbl_selected = tk.Label(bar, text="0 selected",
            fg=TEXT3, bg=BG2, font=("Segoe UI", 9))
        self._lbl_selected.pack(side="left", padx=(12, 10))
        # Clicking the counter clears the checked set
        self._lbl_selected.bind("<Button-1>", lambda e: self._uncheck_all("rec"))

        ttk.Button(bar, text="▶ REC", style="Green.TButton",
                   command=self._rec_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="⏹ Stop", style="Ghost.TButton",
                   command=self._stop_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="☑ Toggle AUTO", style="Accent.TButton",
                   command=self._toggle_auto_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="📁", style="Flat.TButton",
                   command=lambda: os.startfile(self.settings.output_dir)
                   ).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="✕ Remove", style="Red.TButton",
                   command=self._remove_selected).pack(side="left", padx=(0,4), pady=5)
        self._btn_saved = ttk.Button(bar, text="★ Add to Saved", style="Flat.TButton",
                                     command=self._toggle_saved_selected, state="disabled")
        self._btn_saved.pack(side="left", padx=(0,4), pady=5)
        tk.Label(bar, text="🔎", fg=TEXT3, bg=BG2,
                 font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))
        self._v_filter_rec = tk.StringVar()
        ttk.Entry(bar, textvariable=self._v_filter_rec, width=14
                  ).pack(side="left", padx=(2, 0), pady=8)
        self._v_filter_rec.trace_add(
            "write", lambda *a: self._schedule_filter("rec"))
        self._mb_status_rec = self._build_status_menubutton(bar, "rec")
        self._mb_status_rec.pack(side="left", padx=(4, 0), pady=5)

        self._btn_monitor_rec = ttk.Button(bar, text="▶  START MONITOR",
                                            style="Green.TButton",
                                            command=self._toggle_monitor_recorder)
        self._btn_monitor_rec.pack(side="right", padx=10, pady=5)
        ttk.Button(bar, text="⏹  STOP ALL DOWNLOADS", style="Red.TButton",
                   command=self._stop_all_downloads
                   ).pack(side="right", padx=(0, 0), pady=5)
        ttk.Button(bar, text="🧹  CLEAR RECORDER", style="Ghost.TButton",
                   command=self._clear_recorder
                   ).pack(side="right", padx=(0, 6), pady=5)

        # ── Treeview ──
        cols = ("rank", "status", "file", "size", "auto", "saved")
        frame = tk.Frame(p, bg=BG)
        frame.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                   selectmode="extended", style="Dark.Treeview")

        # Tree column (#0) = model name — sortable
        self._tree.heading("#0", text="MODEL  ↕", anchor="w",
                           command=lambda: self._sort_tree("#0"))
        self._tree.column("#0", width=200, minwidth=120)

        self._tree.heading("rank", text="RANK  ↕", anchor="w",
                           command=lambda: self._sort_tree("rank"))
        self._tree.column("rank", width=92, minwidth=92, stretch=False)

        self._tree.heading("status", text="STATUS  ↕", anchor="w",
                           command=lambda: self._sort_tree("status"))
        self._tree.column("status", width=140, minwidth=80)

        self._tree.heading("file", text="CURRENT FILE", anchor="w")
        self._tree.column("file", width=320, minwidth=100)

        self._tree.heading("size", text="SIZE  ↕", anchor="e",
                           command=lambda: self._sort_tree("size"))
        self._tree.column("size", width=90, minwidth=60, anchor="e")

        self._tree.heading("auto", text="AUTO  ↕", anchor="center",
                           command=lambda: self._sort_tree("auto"))
        self._tree.column("auto", width=70, minwidth=50, anchor="center")

        self._tree.heading("saved", text="SAVED  ↕", anchor="center",
                           command=lambda: self._sort_tree("saved"))
        self._tree.column("saved", width=70, minwidth=50, anchor="center")

        self._sort_reverse: dict[str, bool] = {}  # track sort direction per column

        sb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._tree.pack(side="left", fill="both", expand=True)

        # Status color tags
        self._tree.tag_configure("s_offline",   foreground=TEXT3)
        self._tree.tag_configure("s_online",    foreground=GREEN)
        self._tree.tag_configure("s_recording", foreground=ACCENT)
        self._tree.tag_configure("s_error",     foreground=RED)
        self._tree.tag_configure("s_checking",  foreground=YELLOW)
        self._tree.tag_configure("site_hdr",    foreground=ACCENT,
                                  font=("Segoe UI Semibold", 9))

        # Bindings
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<B1-Motion>", lambda e: self._on_tree_drag(e, "rec"))
        self._tree.bind("<ButtonRelease-1>",
                        lambda e: self._end_tree_drag("rec"), add="+")
        self._tree.bind("<<TreeviewSelect>>", lambda e: self._on_tree_select())
        self._tree.bind("<Button-3>", self._on_tree_right_click)
        self._tree.bind("<Control-a>", lambda e: self._select_all(self._tree))

        # Context menu
        self._ctx = tk.Menu(self, tearoff=0, bg=BG3, fg=TEXT, activebackground=BG2,
                            activeforeground=ACCENT, font=UI, relief="flat", bd=0)

    # ── Treeview helpers ──────────────────────────────────────────────────────

    def _ensure_site(self, site: str):
        site_id = f"_site_{site}"
        if self._tree.exists(site_id):
            return
        label = {"chaturbate": "CHATURBATE", "stripchat": "STRIPCHAT",
                 "camsoda": "CAMSODA",
                 "myfreecams": "MYFREECAMS"}.get(site, site.upper())
        self._tree.insert("", "end", iid=site_id, text=f"  {label}",
                          values=("", "", "", "", "", ""), tags=("site_hdr",), open=True)

    # ── Row checkboxes ────────────────────────────────────────────────────────
    # Tk's Treeview has no native checkbox column, and nothing can sit left of
    # the name column — so the checkbox is a ☐/☑ glyph prefixed to the name
    # (same trick as the AUTO column). Clicking the glyph zone toggles it.

    @staticmethod
    def _row_name(tree, iid) -> str:
        return tree.item(iid, "text").lstrip("☐☑ ")

    def _set_row_glyph(self, tree, iid, checked: bool):
        tree.item(iid, text=f"{'☑' if checked else '☐'}  {self._row_name(tree, iid)}")

    def _toggle_check(self, tree, iid, checked: set):
        if iid in checked:
            checked.discard(iid)
        else:
            checked.add(iid)
        self._set_row_glyph(tree, iid, iid in checked)
        if tree is self._tree:
            self._update_selection_label()
        else:
            self._update_saved_count()

    def _check_all_visible(self, which: str):
        """Check every row currently visible (respects active filters)."""
        if which == "rec":
            tree, rows, checked = self._tree, self._rows, self._checked
        else:
            tree, rows, checked = self._stree, self._saved_rows, self._saved_checked
        for iid in rows:
            # attached to a site header = visible (filters detach hidden rows)
            if iid not in checked and tree.exists(iid) and tree.parent(iid):
                checked.add(iid)
                self._set_row_glyph(tree, iid, True)
        self._update_selection_label()
        self._update_saved_count()

    def _uncheck_all(self, which: str):
        if which == "rec":
            tree, checked = self._tree, self._checked
        else:
            tree, checked = self._stree, self._saved_checked
        for iid in list(checked):
            if tree.exists(iid):
                self._set_row_glyph(tree, iid, False)
        checked.clear()
        self._update_selection_label()
        self._update_saved_count()

    # ── Drag selection ────────────────────────────────────────────────────────
    # ttk.Treeview has Shift+click (range) and Ctrl+click (toggle) built in,
    # but no press-and-drag selection — added here: drag from a row across
    # others to select the whole range, with edge auto-scroll.

    @staticmethod
    def _visible_rows(tree) -> list:
        rows = []
        for hdr in tree.get_children(""):
            rows.extend(tree.get_children(hdr))
        return rows

    def _on_tree_drag(self, event, which: str):
        anchor = self._drag_anchor.get(which)
        tree = self._tree if which == "rec" else self._stree
        if not anchor or not tree.exists(anchor):
            return
        h = tree.winfo_height()
        if event.y < 12:
            tree.yview_scroll(-1, "units")
        elif event.y > h - 12:
            tree.yview_scroll(1, "units")
        cur = tree.identify_row(min(max(event.y, 1), h - 1))
        prefix = "_site_" if which == "rec" else "_ssite_"
        if not cur or cur.startswith(prefix):
            return "break"
        rows = self._visible_rows(tree)
        try:
            i0, i1 = rows.index(anchor), rows.index(cur)
        except ValueError:
            return "break"
        if i0 > i1:
            i0, i1 = i1, i0
        tree.selection_set(rows[i0:i1 + 1])
        return "break"

    def _end_tree_drag(self, which: str):
        self._drag_anchor[which] = None

    def _check_selection(self, which: str):
        """Convert the current click-selection into checked boxes."""
        if which == "rec":
            tree, checked, prefix = self._tree, self._checked, "_site_"
        else:
            tree, checked, prefix = self._stree, self._saved_checked, "_ssite_"
        for iid in tree.selection():
            if not iid.startswith(prefix) and iid not in checked:
                checked.add(iid)
                self._set_row_glyph(tree, iid, True)
        self._update_selection_label()
        self._update_saved_count()

    def _update_selection_label(self):
        n_chk = len(self._checked)
        if n_chk:
            # Checked set takes priority — make that visible. Click to clear.
            self._lbl_selected.configure(text=f"✓ {n_chk} checked", fg=ACCENT)
            return
        sel = [iid for iid in self._tree.selection()
               if not iid.startswith("_site_")]
        n = len(sel)
        self._lbl_selected.configure(
            text=f"{n} selected" if n else "0 selected",
            fg=TEXT if n else TEXT3)

    def _on_tree_select(self):
        self._update_selection_label()
        self._update_saved_btn()

    def _update_saved_btn(self):
        """Update the Add/Remove Saved button text based on selection."""
        keys = self._get_selected_keys()
        if not keys:
            self._btn_saved.configure(text="★ Add to Saved", state="disabled")
            return
        self._btn_saved.configure(state="normal")
        site, name = keys[0].split(":", 1)
        if self._saved_key(name, site) in self._saved_data:
            self._btn_saved.configure(text="★ Remove from Saved")
        else:
            self._btn_saved.configure(text="★ Add to Saved")

    def _toggle_saved_selected(self):
        """Add or remove selected model(s) from Saved Models."""
        keys = self._get_selected_keys()
        if not keys:
            return
        site, name = keys[0].split(":", 1)
        if self._saved_key(name, site) in self._saved_data:
            for key in keys:
                s, n = key.split(":", 1)
                self._remove_saved(self._saved_key(n, s))
        else:
            for key in keys:
                s, n = key.split(":", 1)
                self._add_to_saved(n, s)

    def _get_selected_keys(self) -> list[str]:
        """Model keys for bulk actions: the checked set when any row is
        checked, otherwise the click-selection (site headers skipped)."""
        if self._checked:
            return [k for k in self._checked if self._tree.exists(k)]
        return [iid for iid in self._tree.selection()
                if not iid.startswith("_site_")]

    def _on_tree_click(self, event):
        """Toggle the row checkbox (name-column glyph) or AUTO."""
        region = self._tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        col = self._tree.identify_column(event.x)
        iid = self._tree.identify_row(event.y)
        if not iid or iid.startswith("_site_"):
            return
        modified = event.state & 0x0005  # Shift (1) / Control (4) held
        if col == "#0" and event.x <= 48 and not modified:
            # Plain click on the glyph toggles the check; with Shift/Ctrl the
            # click falls through so native range/toggle selection works
            # everywhere in the row.
            self._toggle_check(self._tree, iid, self._checked)
            return "break"  # don't let the click also change the selection
        # Click a star in the RANK column (#1) to set 1-5 stars; clicking the
        # star that's already the rank clears it back to 0.
        if col == "#1" and not modified:
            site, name = iid.split(":", 1)
            bbox = self._tree.bbox(iid, col)
            if bbox:
                bx, _, bw, _ = bbox
                star = max(1, min(5, int((event.x - bx) / (bw / 5)) + 1))
                cur = self._get_rank(name, site)
                self._set_rank_many([(name, site)], 0 if cur == star else star)
            return "break"
        if not modified:
            self._drag_anchor["rec"] = iid  # start of a possible drag-select
        # Columns: #0 tree (name), #1=rank, #2=status, #3=file, #4=size,
        # #5=auto, #6=saved.
        if col == "#5":
            cur = self._auto_rec.get(iid, False)
            new_val = not cur
            self._auto_rec[iid] = new_val
            self._tree.set(iid, "auto", "☑" if new_val else "☐")
            self._persist_models()

    def _on_tree_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid or iid.startswith("_site_"):
            return
        # Add right-clicked row to selection if not already selected
        if iid not in self._tree.selection():
            self._tree.selection_set(iid)
        sel = self._get_selected_keys()
        n = len(sel)
        m = self._ctx
        m.delete(0, "end")

        if n == 1:
            # Single-model context menu
            site, name = iid.split(":", 1)
            m.add_command(label=f"▶  Start Recording  {name}",
                          command=lambda: self._toggle_rec(iid, name, site))
            m.add_command(label=f"⏹  Stop Recording  {name}",
                          command=lambda: self._stop_async(name, site))
            m.add_separator()
            auto = self._auto_rec.get(iid, False)
            m.add_command(label=f"{'☑' if auto else '☐'}  Auto-Record",
                          command=lambda: self._toggle_auto_single(iid))
            m.add_cascade(label=f"🎞  Max Quality  ({self._quality_text(iid)})",
                          menu=self._build_quality_menu([iid]))
            sid_check = self._saved_key(name, site)
            if sid_check in self._saved_data:
                m.add_command(label="✕  Remove from Saved Models",
                              command=lambda s=sid_check: self._remove_saved(s))
            else:
                m.add_command(label="⭐  Add to Saved Models",
                              command=lambda: self._add_to_saved(name, site))
            m.add_separator()
            m.add_command(label="🔗  Copy Model URL",
                          command=lambda: self._copy_model_url(name, site))
            m.add_command(label="🌐  Open in Browser",
                          command=lambda: self._open_in_browser([iid]))
            m.add_command(label="📁  Open Output Folder",
                          command=lambda: os.startfile(self.settings.output_dir))
            m.add_command(label="✕  Remove Model",
                          command=lambda: self._remove_model(iid, name, site))
        else:
            # Multi-model context menu
            m.add_command(label=f"▶  Start Recording  ({n} selected)",
                          command=self._rec_selected)
            m.add_command(label=f"⏹  Stop Recording  ({n} selected)",
                          command=self._stop_selected)
            m.add_separator()
            m.add_command(label=f"☑  Toggle AUTO  ({n} selected)",
                          command=self._toggle_auto_selected)
            m.add_cascade(label=f"🎞  Max Quality  ({n} selected)",
                          menu=self._build_quality_menu(sel))
            m.add_separator()
            m.add_command(label=f"🌐  Open in Browser  ({n} selected)",
                          command=lambda s=sel: self._open_in_browser(s))
            m.add_command(label=f"📋  Copy as OneTab List  ({n} selected)",
                          command=lambda s=sel: self._copy_onetab(s))
            m.add_command(label="📁  Open Output Folder",
                          command=lambda: os.startfile(self.settings.output_dir))
            m.add_command(label=f"✕  Remove  ({n} selected)",
                          command=self._remove_selected)

        # Set Rank submenu — one row or the whole selection
        m.add_separator()
        rank_items = [(k.split(":", 1)[1], k.split(":", 1)[0]) for k in sel]
        rank_lbl = "Set Rank" if n == 1 else f"Set Rank  ({n} selected)"
        rank_menu = tk.Menu(m, tearoff=0, bg=BG3, fg=TEXT,
                            activebackground=BG2, activeforeground=ACCENT,
                            font=UI, relief="flat", bd=0)
        for r in (5, 4, 3, 2, 1):
            rank_menu.add_command(
                label=self._rank_stars(r),
                command=lambda it=rank_items, rr=r: self._set_rank_many(it, rr))
        rank_menu.add_separator()
        rank_menu.add_command(
            label="☆  Clear rank",
            command=lambda it=rank_items: self._set_rank_many(it, 0))
        m.add_cascade(label=f"⭐  {rank_lbl}", menu=rank_menu)

        m.add_separator()
        raw_sel = [i for i in self._tree.selection()
                   if not i.startswith("_site_")]
        if raw_sel:
            m.add_command(label=f"☑  Check Selected  ({len(raw_sel)})",
                          command=lambda: self._check_selection("rec"))
        m.add_command(label="☑  Check All Visible",
                      command=lambda: self._check_all_visible("rec"))
        if self._checked:
            m.add_command(label=f"☐  Uncheck All  ({len(self._checked)})",
                          command=lambda: self._uncheck_all("rec"))

        m.tk_popup(event.x_root, event.y_root)

    def _quality_text(self, key: str) -> str:
        h = self._model_q.get(key, 0)
        return _quality_label(h) if h else "Default"

    def _build_quality_menu(self, keys: list[str]) -> tk.Menu:
        sub = tk.Menu(self._ctx, tearoff=0, bg=BG3, fg=TEXT, activebackground=BG2,
                      activeforeground=ACCENT, font=UI, relief="flat", bd=0)
        cur = self._model_q.get(keys[0], 0) if len(keys) == 1 else None
        opts = [("Default (use global setting)", 0)] + \
               [(lbl, h) for lbl, h in QUALITY_OPTIONS.items() if h]
        for lbl, h in opts:
            mark = "●  " if cur == h else "    "
            sub.add_command(label=f"{mark}{lbl}",
                            command=lambda h=h, ks=keys: self._set_quality(ks, h))
        return sub

    def _set_quality(self, keys: list[str], height: int):
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

    def _toggle_auto_single(self, key: str):
        self._set_auto(key, not self._auto_rec.get(key, False))

    def _set_auto(self, key: str, val: bool):
        self._auto_rec[key] = val
        self._tree.set(key, "auto", "☑" if val else "☐")
        self._persist_models()

    def _stop_all_downloads(self):
        """Force-stop every active download (all sites) and uncheck every
        AUTO toggle so the monitor doesn't restart them."""
        if not messagebox.askyesno(
                "Stop all downloads",
                "Force-stop ALL active downloads and uncheck AUTO on every model?"):
            return
        for key in list(self._rows):
            self._auto_rec[key] = False
            if self._tree.exists(key):
                self._tree.set(key, "auto", "☐")
        self._persist_models()
        self._log_add("Stopping all downloads…", "warn")
        # Killing sessions waits on graceful stops — keep it off the UI thread
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="stop-all-dl").start()

    def _clear_recorder(self):
        """Clean-slate the Recorder: stop the monitor, stop every download,
        uncheck all AUTO, and remove every model (all sites). Leaves the app
        ready to use. Saved Models are untouched."""
        if not messagebox.askyesno(
                "Clear recorder",
                "Stop everything and remove ALL models from the Recorder?\n\n"
                "This stops the monitor, all active downloads, clears AUTO, and "
                "empties the Recorder list. Saved Models are kept."):
            return
        self._do_clear_recorder()

    def _do_clear_recorder(self, via_api: bool = False):
        """Clean-slate the Recorder without a dialog. Shared by the button
        (after confirm) and the bot API (/clear)."""
        # Stop the recorder monitor so it can't re-add/restart anything
        if self._monitoring_recorder:
            self._toggle_monitor_recorder()
        # Uncheck every AUTO, then force-stop all active downloads off-thread
        for key in list(self._rows):
            self._auto_rec[key] = False
            if self._tree.exists(key):
                self._tree.set(key, "auto", "☐")
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="clear-stop-all").start()
        # Remove every model from the Recorder (reuses the no-dialog remover,
        # which handles tree rows, headers, persistence, and stats)
        for key in list(self._rows):
            site, name = key.split(":", 1)
            self._do_remove_from_recorder(name, site)
        self._uncheck_all("rec")
        self._update_stats()
        self._log_add("Recorder cleared%s." % (" (via API)" if via_api else ""),
                      "warn")

    # ── Extension/bot API actions (no modal dialogs) ─────────────────────────
    def _api_stop_all(self):
        """API: same as the Stop-All button but without the confirm dialog."""
        for key in list(self._rows):
            self._auto_rec[key] = False
            if self._tree.exists(key):
                self._tree.set(key, "auto", "☐")
        self._persist_models()
        self._log_add("Stopping all downloads (via API)…", "warn")
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="stop-all-dl-api").start()

    def _api_set_monitor(self, which: str, enabled: bool):
        """API: bring the recorder/saved monitor to *enabled* (idempotent)."""
        current = (self._monitoring_recorder if which == "recorder"
                   else self._monitoring_saved)
        if enabled == current:
            return  # already in the desired state
        if which == "recorder":
            self._toggle_monitor_recorder()
        else:
            self._toggle_monitor_saved()

    def _api_set_pipeline(self, enabled: bool):
        """API: bring the Telegram pipeline to *enabled* (idempotent)."""
        running = bool(self.pipeline and self.pipeline.running)
        if enabled == running:
            return
        self._toggle_pipeline(silent=True)

    def _api_quit(self):
        """API: graceful shutdown with no confirm dialog (mirrors _on_close
        minus the prompt). Used by the bot's `close` command."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._lbl_hdr_status.configure(text="● STOPPING…", fg=ORANGE)
        self._stop_api_server()
        self._remove_tray()
        self._save_settings()
        def _shutdown():
            try:
                self.recorder.stop_monitor()
            except Exception:
                pass
            if hasattr(self, "pipeline"):
                try:
                    self.pipeline.stop()
                except Exception:
                    pass
            try:
                self.after(0, self.destroy)
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=_shutdown, daemon=True, name="shutdown-api").start()

    def _select_all(self, tree: ttk.Treeview):
        """Select all model rows (skip site/section headers)."""
        items = []
        for site_id in tree.get_children(""):
            items.extend(tree.get_children(site_id))
        if items:
            tree.selection_set(items)
        return "break"

    @staticmethod
    def _size_sort_key(val: str) -> float:
        """Parse '12.3 MB' / '1.23 GB' / '—' into a float for numeric sort."""
        v = val.strip()
        if not v or v == "—":
            return -1.0
        try:
            if "GB" in v:
                return float(v.replace("GB", "").strip()) * 1024
            if "MB" in v:
                return float(v.replace("MB", "").strip())
        except ValueError:
            pass
        return -1.0

    def _sort_generic(self, tree: ttk.Treeview, col: str,
                      site_prefix: str, sort_state: dict,
                      col_labels: dict):
        """Sort model rows under every site header in *tree* by *col*."""
        reverse = sort_state.get(col, False)
        sort_state[col] = not reverse

        for site_id in list(tree.get_children("")):
            if not site_id.startswith(site_prefix):
                continue
            children = list(tree.get_children(site_id))
            if not children:
                continue
            if col == "#0":
                # lstrip the checkbox glyph so checked rows don't sort apart
                children.sort(
                    key=lambda iid: tree.item(iid, "text").lstrip("☐☑ ").lower(),
                    reverse=reverse)
            elif col == "size":
                children.sort(
                    key=lambda iid: self._size_sort_key(tree.set(iid, "size")),
                    reverse=reverse)
            elif col == "rank":
                children.sort(
                    key=lambda iid: tree.set(iid, "rank").count("★"),
                    reverse=reverse)
            else:
                children.sort(
                    key=lambda iid: tree.set(iid, col).strip().lower(),
                    reverse=reverse)
            for idx, iid in enumerate(children):
                tree.move(iid, site_id, idx)

        arrow = " ▼" if reverse else " ▲"
        for c, label in col_labels.items():
            tree.heading(c, text=f"{label}  {arrow}" if c == col
                         else f"{label}  ↕")

    def _sort_tree(self, col: str):
        self._sort_generic(
            self._tree, col,
            site_prefix="_site_",
            sort_state=self._sort_reverse,
            col_labels={"#0": "MODEL", "rank": "RANK", "status": "STATUS",
                        "size": "SIZE", "auto": "AUTO", "saved": "SAVED"},
        )

    def _sort_stree(self, col: str):
        self._sort_generic(
            self._stree, col,
            site_prefix="_ssite_",
            sort_state=self._stree_sort_reverse,
            col_labels={"#0": "MODEL", "rank": "RANK",
                        "status": "STATUS", "size": "SIZE"},
        )

    # ── Model management ─────────────────────────────────────────────────────

    def _parse_model_input(self, raw: str) -> tuple[str, str]:
        """Parse a username or URL into (name, site).
        Supports:
          chaturbate.com/username/
          stripchat.com/username/
          camsoda.com/username/
          myfreecams.com/#username   (model lives in the URL hash fragment)
          plain username (uses the site dropdown)
        """
        raw = raw.strip().lower()
        # Strip protocol
        for prefix in ("https://", "http://", "www."):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]

        # MyFreeCams profiles are single-page-app URLs: the model name is in
        # the hash fragment (myfreecams.com/#name), not the path.
        if raw.startswith("myfreecams.com"):
            frag = raw.split("#", 1)[1] if "#" in raw else ""
            frag = frag.lstrip("/")
            if frag.startswith("model/"):
                frag = frag[len("model/"):]
            username = frag.split("/")[0].split("?")[0]
            return username.strip("/"), "myfreecams"

        # Detect site from domain
        for domain, site in (("chaturbate.com", "chaturbate"),
                             ("stripchat.com",  "stripchat"),
                             ("camsoda.com",    "camsoda")):
            if raw.startswith(domain):
                parts = raw.split("/")
                username = parts[1] if len(parts) > 1 else ""
                return username.strip("/"), site

        # Plain username — use the site dropdown
        return raw.strip("/"), self._c_site.get()

    def _auto_detect_site(self):
        """Auto-switch the site dropdown when a URL is typed/pasted."""
        text = self._e_name.get().strip().lower()
        if "chaturbate.com" in text:
            self._c_site.set("chaturbate")
        elif "stripchat.com" in text:
            self._c_site.set("stripchat")
        elif "camsoda.com" in text:
            self._c_site.set("camsoda")
        elif "myfreecams.com" in text:
            self._c_site.set("myfreecams")

    def _add_model(self):
        raw = self._e_name.get().strip()
        if not raw:
            messagebox.showwarning("Input required", "Enter a model username or link.")
            return
        name, site = self._parse_model_input(raw)
        if not name:
            messagebox.showwarning("Input required", "Could not extract a username from the input.")
            return
        key = f"{site}:{name}"
        if key in self._rows:
            messagebox.showinfo("Already added", f"{name} ({site}) is already in the list.")
            return
        self.recorder.add_model(name, site, "recorder")
        self._insert_model(name, site)
        self._e_name.delete(0, "end")
        self._persist_models()
        self._update_stats()
        self._log_add(f"Added model: {name} ({site})", "accent")

    def _insert_model(self, name: str, site: str, auto_rec: bool = False):
        key = f"{site}:{name}"
        self._ensure_site(site)
        parent = f"_site_{site}"
        auto_text  = "☑" if auto_rec else "☐"
        saved_text = "✔️" if self._saved_key(name, site) in self._saved_data else "❌"
        self._tree.insert(parent, "end", iid=key, text=f"☐  {name}",
                          values=(self._rank_stars(self._get_rank(name, site)),
                                  "●  OFFLINE", "—", "—", auto_text, saved_text),
                          tags=("s_offline",))
        self._rows[key] = True
        self._auto_rec[key] = auto_rec
        if (self._v_filter_rec.get().strip()
                or self._status_filter_set("rec") is not None):
            self._schedule_filter("rec")  # respect an active filter

    def _do_remove_from_recorder(self, name: str, site: str):
        """Remove a model from the Recorder tab without a confirm dialog.
        Shared by the right-click flow (after confirm) and the extension API."""
        key = f"{site}:{name}"
        self.recorder.remove_model(name, site, "recorder")
        if self._tree.exists(key):
            self._tree.delete(key)
        self._rows.pop(key, None)
        self._auto_rec.pop(key, None)
        self._model_q.pop(key, None)
        self._checked.discard(key)
        site_id = f"_site_{site}"
        # Filtered-out rows are detached (parentless), so check _rows — not
        # get_children() — or we'd delete a header that still owns hidden rows.
        if (self._tree.exists(site_id)
                and not any(k.split(":", 1)[0] == site for k in self._rows)):
            self._tree.delete(site_id)
        self._persist_models()
        self._update_stats()
        self._update_selection_label()
        self._log_add(f"Removed: {name} ({site})", "warn")

    def _remove_model(self, key: str, name: str, site: str):
        if not messagebox.askyesno("Remove model", f"Remove {name} ({site})?"):
            return
        self._do_remove_from_recorder(name, site)

    def _remove_selected(self):
        keys = self._get_selected_keys()
        if not keys:
            return
        names = ", ".join(k.split(":")[1] for k in keys)
        if not messagebox.askyesno("Remove selected", f"Remove {len(keys)} model(s)?\n{names}"):
            return
        for key in keys:
            site, name = key.split(":", 1)
            self.recorder.remove_model(name, site, "recorder")
            if self._tree.exists(key):
                self._tree.delete(key)
            self._rows.pop(key, None)
            self._auto_rec.pop(key, None)
            self._model_q.pop(key, None)
            self._checked.discard(key)
            site_id = f"_site_{site}"
            # _rows includes filter-detached rows; get_children() doesn't
            if (self._tree.exists(site_id)
                    and not any(k.split(":", 1)[0] == site for k in self._rows)):
                self._tree.delete(site_id)
            self._log_add(f"Removed: {name} ({site})", "warn")
        self._persist_models()
        self._update_stats()
        self._update_selection_label()

    _SITE_URLS = {
        "chaturbate": "https://chaturbate.com/{}/",
        "stripchat":  "https://stripchat.com/{}",
        "camsoda":    "https://www.camsoda.com/{}",
        "myfreecams": "https://www.myfreecams.com/#{}",
    }

    def _copy_model_url(self, name: str, site: str):
        url = self._SITE_URLS.get(site, "https://{}/").format(name)
        self.clipboard_clear()
        self.clipboard_append(url)
        self._log_add(f"Copied URL: {url}")

    def _keys_to_models(self, keys: list, saved: bool = False) -> list:
        """Map tree row ids to (name, site, url) tuples.
        Recorder keys are "site:name"; saved keys are "saved:site:name"."""
        out = []
        for k in keys:
            if saved:
                _, site, name = k.split(":", 2)
            else:
                site, name = k.split(":", 1)
            url = self._SITE_URLS.get(site, "https://{}/").format(name)
            out.append((name, site, url))
        return out

    def _open_in_browser(self, keys: list, saved: bool = False):
        items = self._keys_to_models(keys, saved)
        if len(items) > 10 and not messagebox.askyesno(
                "Open in Browser",
                f"Open {len(items)} tabs in your browser?"):
            return
        import webbrowser
        for _, _, url in items:
            webbrowser.open(url)
        self._log_add(f"Opened {len(items)} model page(s) in browser")

    def _copy_onetab(self, keys: list, saved: bool = False):
        """Copy models in OneTab's import format: one "URL | title" per line.
        Paste into OneTab → Import / Export URLs."""
        items = self._keys_to_models(keys, saved)
        text = "\n".join(f"{url} | {name} ({site})"
                         for name, site, url in items)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log_add(f"Copied {len(items)} model(s) as OneTab list")

    def _stop_async(self, name: str, site: str):
        """Stop a recording on a worker thread — graceful_stop blocks up to
        ~15 s per process, which froze the GUI when run on the Tk thread."""
        def _do():
            self.recorder.stop_recording(name, site)
            self.after(0, lambda: self._log_add(
                f"Stopped recording: {name} ({site})", "warn"))
        threading.Thread(target=_do, daemon=True,
                         name=f"stop-{site}-{name}").start()

    def _stop_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            site, name = key.split(":", 1)
            cfg = self.recorder.models.get(key)
            if cfg and cfg.status == ModelStatus.RECORDING:
                self._stop_async(name, site)

    def _rec_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            site, name = key.split(":", 1)
            cfg = self.recorder.models.get(key)
            if cfg and cfg.status in (ModelStatus.ONLINE, ModelStatus.OFFLINE, ModelStatus.PRIVATE):
                self._toggle_rec(key, name, site)

    def _toggle_auto_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            self._toggle_auto_single(key)

    def _size_sweep_loop(self):
        """Worker thread: every 3 s stat all recording files at once and post
        ONE batched UI update (replaces a per-model after() chain that did
        blocking getsize calls on the Tk thread)."""
        while True:
            time.sleep(3)
            with self.recorder._lock:
                snap = [(k, c.session.current_file)
                        for k, c in self.recorder.models.items()
                        if c.session and c.session.current_file]
            sizes = {}
            for key, path in snap:
                try:
                    sizes[key] = os.path.getsize(path)
                except OSError:
                    pass
            if not sizes:
                continue
            try:
                self.after(0, lambda s=sizes: self._apply_sizes(s))
            except (tk.TclError, RuntimeError):
                return  # app is shutting down

    def _apply_sizes(self, sizes: dict):
        for key, size in sizes.items():
            mb = size / (1024 * 1024)
            txt = f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"
            if self._size_cache.get(key) == txt:
                continue  # unchanged — skip the Treeview write
            self._size_cache[key] = txt
            if key in self._rows and self._tree.exists(key):
                self._tree.set(key, "size", txt)
            sid = f"saved:{key}"
            if sid in self._saved_rows and self._stree.exists(sid):
                self._stree.set(sid, "size", txt)

    def _stats_tick(self):
        try:
            if self._stats_dirty:
                self._stats_dirty = False
                self._update_stats()
        finally:
            self.after(500, self._stats_tick)

    # ── Record toggle (per model) ─────────────────────────────────────────────

    def _toggle_rec(self, key: str, name: str, site: str):
        with self.recorder._lock:
            cfg = self.recorder.models.get(key)
        if not cfg:
            return
        if cfg.status == ModelStatus.RECORDING:
            self._stop_async(name, site)
        else:
            def _do():
                ok = self.recorder.start_recording(name, site)
                if not ok:
                    self.after(0, lambda: self._log_add(
                        f"{name} ({site}) is offline — cannot record.", "warn"))
            threading.Thread(target=_do, daemon=True).start()

    # ── Monitor toggles (per-tab, independent) ────────────────────────────────

    def _sync_recorder_settings(self):
        """Push current UI settings into the engine before (re)starting a monitor."""
        self.recorder.output_dir     = self._e_folder.get().strip() or self.settings.output_dir
        self.recorder.max_size_mb    = self._parse_int(self._v_maxsize.get())
        self.recorder.check_interval = self._parse_int(self._v_interval.get(), 30)

    def _refresh_hdr_status(self):
        r, s = self._monitoring_recorder, self._monitoring_saved
        if r and s:
            self._lbl_hdr_status.configure(text="● MONITORING (R+S)", fg=GREEN)
        elif r:
            self._lbl_hdr_status.configure(text="● RECORDER ACTIVE", fg=GREEN)
        elif s:
            self._lbl_hdr_status.configure(text="● SCANNER ACTIVE", fg=GREEN)
        else:
            self._lbl_hdr_status.configure(text="● IDLE", fg=TEXT3)

    def _toggle_monitor_recorder(self):
        if self._monitoring_recorder:
            # stop_monitor flushes every active ffmpeg (seconds) — keep it
            # off the Tk thread so the GUI stays responsive.
            threading.Thread(target=self.recorder.stop_monitor,
                             args=("recorder",), daemon=True,
                             name="stop-mon-recorder").start()
            self._monitoring_recorder = False
            self._btn_monitor_rec.configure(text="▶  START MONITOR", style="Green.TButton")
            self._log_add("Recorder monitor stopped.", "warn")
        else:
            self._sync_recorder_settings()
            if self.recorder.start_monitor("recorder"):
                self._monitoring_recorder = True
                self._btn_monitor_rec.configure(text="⏹  STOP MONITOR", style="Red.TButton")
                self._log_add("Recorder monitor started.", "success")
            else:
                self._log_add("Recorder monitor FAILED to start — "
                              "see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)", "error")
        self._refresh_hdr_status()

    def _toggle_monitor_saved(self):
        if self._monitoring_saved:
            threading.Thread(target=self.recorder.stop_monitor,
                             args=("saved",), daemon=True,
                             name="stop-mon-saved").start()
            self._monitoring_saved = False
            self._btn_monitor_saved.configure(text="▶  START SCANNER", style="Green.TButton")
            self._log_add("Saved Models scanner stopped.", "warn")
        else:
            self._sync_recorder_settings()
            # Lazy saved tab: make sure the rows exist and the engine knows
            # the watchlist before the scanner starts.
            self._populate_saved_tab()
            self._register_saved_models()
            if self.recorder.start_monitor("saved"):
                self._monitoring_saved = True
                self._btn_monitor_saved.configure(text="⏹  STOP SCANNER", style="Red.TButton")
                self._log_add("Saved Models scanner started.", "success")
            else:
                self._log_add("Saved Models scanner FAILED to start — "
                              "see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)", "error")
        self._refresh_hdr_status()

    def _sync_monitor_buttons(self):
        """Watchdog: if a monitor thread died (crash guard resets its running
        flag), flip the button/header back so START works again instead of
        silently pretending to monitor."""
        if self._monitoring_recorder and not self.recorder._running.get("recorder"):
            self._monitoring_recorder = False
            self._btn_monitor_rec.configure(text="▶  START MONITOR",
                                            style="Green.TButton")
            self._log_add("Recorder monitor is no longer running.", "error")
            self._refresh_hdr_status()
        if self._monitoring_saved and not self.recorder._running.get("saved"):
            self._monitoring_saved = False
            self._btn_monitor_saved.configure(text="▶  START SCANNER",
                                              style="Green.TButton")
            self._log_add("Saved Models scanner is no longer running.", "error")
            self._refresh_hdr_status()
        self.after(5000, self._sync_monitor_buttons)

    # ── Privacy Mode ──────────────────────────────────────────────────────────
    # When enabled and the window sits untouched for PRIVACY_IDLE_SECONDS, a
    # full-window starfield covers the UI (and the title is blanked) so nothing
    # is readable while AFK. A click inside or a window move asks to exit;
    # the mode re-arms after the next idle period until the box is unchecked.

    def _privacy_init(self):
        self._last_activity      = time.time()
        self._privacy_canvas     = None
        self._privacy_prompting  = False
        self._privacy_anim_job   = None
        self._privacy_title      = ""
        for seq in ("<Motion>", "<KeyPress>", "<Button>", "<MouseWheel>"):
            self.bind_all(seq, self._privacy_touch, add="+")
        # Children share the root's bindtag, so filter to root-only events
        self.bind("<Configure>", self._privacy_on_configure, add="+")
        self.after(500, self._privacy_tick)

    def _privacy_touch(self, event=None):
        self._last_activity = time.time()

    def _privacy_on_configure(self, event):
        if event.widget is not self:
            return
        if self._privacy_canvas is not None:
            self._privacy_prompt_exit()   # window moved/resized while covered
        else:
            self._last_activity = time.time()

    def _privacy_tick(self):
        try:
            if (self._v_privacy.get()
                    and self._privacy_canvas is None
                    and not self._privacy_prompting
                    and self.state() in ("normal", "zoomed")
                    and time.time() - self._last_activity >= PRIVACY_IDLE_SECONDS):
                self._privacy_engage()
        finally:
            self.after(500, self._privacy_tick)

    def _privacy_engage(self):
        self._privacy_title = self.title()
        self.title(" ")
        c = tk.Canvas(self, bg="#03030a", highlightthickness=0)
        c.place(x=0, y=0, relwidth=1, relheight=1)
        c.bind("<Button-1>", lambda e: self._privacy_prompt_exit())
        self._privacy_canvas = c
        self._privacy_scene_init()
        self._privacy_animate()

    def _privacy_disengage(self):
        if self._privacy_anim_job:
            self.after_cancel(self._privacy_anim_job)
            self._privacy_anim_job = None
        if self._privacy_canvas is not None:
            self._privacy_canvas.destroy()
            self._privacy_canvas = None
        if self._privacy_title:
            self.title(self._privacy_title)
        self._last_activity = time.time()

    def _privacy_prompt_exit(self):
        if self._privacy_prompting or self._privacy_canvas is None:
            return
        self._privacy_prompting = True
        try:
            if messagebox.askyesno("Privacy Mode", "Exit privacy mode?",
                                   parent=self):
                self._privacy_disengage()
        finally:
            self._privacy_prompting = False
            self._last_activity = time.time()

    def _privacy_scene_init(self):
        c = self._privacy_canvas
        self.update_idletasks()
        w = max(self.winfo_width(),  200)
        h = max(self.winfo_height(), 200)
        # Layered nebulas: a wide stippled base + a brighter stippled core per
        # cloud fakes a soft alpha glow (Tk canvas has no real transparency)
        self._neb_items = []
        for base, glow in (("#141031", "#241d56"), ("#0c1c33", "#17345e"),
                           ("#1c0f2e", "#352052"), ("#0e2430", "#1b4254"),
                           ("#26101b", "#471f33")):
            r = random.randint(100, 190)
            x, y = random.uniform(0, w), random.uniform(0, h)
            outer = c.create_oval(x - r, y - r, x + r, y + r,
                                  fill=base, outline="", stipple="gray50")
            ri = r * 0.55
            inner = c.create_oval(x - ri, y - ri, x + ri, y + ri,
                                  fill=glow, outline="", stipple="gray25")
            self._neb_items.append([outer, inner,
                                    random.uniform(-0.35, 0.35),
                                    random.uniform(-0.25, 0.25), r])
        # Stars: warp streaks in centered unit coords with depth z, per-star
        # speed (parallax) and color temperature, plus a twinkle phase and
        # the last width written (so unchanged widths skip itemconfigure)
        self._star_items = []
        palette = ("#ffffff", "#ffffff", "#dfe8ff", "#cfd8f5",
                   "#ffe9d0", "#bcd2ff", "#f5d7e3")
        for _ in range(170):
            item = c.create_line(0, 0, 0, 0, fill=random.choice(palette),
                                 width=1, capstyle="round")
            self._star_items.append([item,
                                     random.uniform(-1, 1),
                                     random.uniform(-1, 1),
                                     random.uniform(0.05, 1.0),
                                     random.uniform(0.003, 0.010),
                                     random.uniform(0, math.tau),
                                     1.0])
        # One shooting star, reused between flights
        self._comet = c.create_line(0, 0, 0, 0, fill="#eaf2ff",
                                    width=2, capstyle="round")
        self._comet_state = None
        self._privacy_frame = 0.0
        self._privacy_last = time.monotonic()

    def _privacy_animate(self):
        c = self._privacy_canvas
        if c is None:
            return
        # Window minimized/hidden: skip the rendering work entirely, keep a
        # slow heartbeat so the scene resumes when the window comes back.
        if self.state() not in ("normal", "zoomed"):
            self._privacy_last = time.monotonic()
            self._privacy_anim_job = self.after(500, self._privacy_animate)
            return
        # Time-based motion: when the event loop is busy and frames arrive
        # late, advance the scene by elapsed time instead of one fixed step
        # per frame — late frames no longer make the stars freeze-and-jump.
        now = time.monotonic()
        dt = min(now - self._privacy_last, 0.2)
        self._privacy_last = now
        step = dt / 0.033 if dt > 0 else 1.0
        w = max(c.winfo_width(), 2)
        h = max(c.winfo_height(), 2)
        cx, cy = w / 2, h / 2
        self._privacy_frame += step
        f = self._privacy_frame
        for s in self._star_items:
            item, x, y, z, spd, ph, last_w = s
            z -= spd * step
            if z <= 0.03:
                x, y, z = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
            px = cx + (x / z) * cx * 0.85
            py = cy + (y / z) * cy * 0.85
            if px < -20 or px > w + 20 or py < -20 or py > h + 20:
                x, y, z = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
                px = cx + x * cx * 0.85
                py = cy + y * cy * 0.85
            # Tail end sampled at a slightly earlier depth — close, fast stars
            # stretch into warp streaks; distant ones stay near-points
            zt = min(z + spd * 7, 1.0)
            qx = cx + (x / zt) * cx * 0.85
            qy = cy + (y / zt) * cy * 0.85
            depth = 1.0 - z
            width = max(1.0, 3.2 * depth + 0.7 * math.sin(f * 0.18 + ph))
            c.coords(item, qx, qy, px, py)
            if abs(width - last_w) >= 0.2:
                c.itemconfigure(item, width=width)
                s[6] = width
            s[1], s[2], s[3] = x, y, z
        # Shooting star: rare, fast diagonal streak with a long tail
        if self._comet_state is None:
            if random.random() < 0.006 * step:
                self._comet_state = [random.uniform(0.1, 0.9) * w, -12.0,
                                     random.uniform(-7, 7),
                                     random.uniform(8, 13)]
        else:
            st = self._comet_state
            st[0] += st[2] * step
            st[1] += st[3] * step
            c.coords(self._comet, st[0] - st[2] * 5, st[1] - st[3] * 5,
                     st[0], st[1])
            if st[0] < -80 or st[0] > w + 80 or st[1] > h + 80:
                c.coords(self._comet, 0, 0, 0, 0)
                self._comet_state = None
        for n in self._neb_items:
            outer, inner, dx, dy, r = n
            c.move(outer, dx * step, dy * step)
            c.move(inner, dx * step, dy * step)
            x0, y0, x1, y1 = c.coords(outer)
            mx = my = 0
            if x1 < 0:
                mx = w + 2 * r
            elif x0 > w:
                mx = -(w + 2 * r)
            if y1 < 0:
                my = h + 2 * r
            elif y0 > h:
                my = -(h + 2 * r)
            if mx or my:
                c.move(outer, mx, my)
                c.move(inner, mx, my)
        self._privacy_anim_job = self.after(33, self._privacy_animate)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _cb_status(self, key: str, status: ModelStatus, detail: str):
        self.after(0, lambda: self._apply_status(key, status, detail))

    def _apply_status(self, key: str, status: ModelStatus, detail: str):
        # Mirror status into the Saved Models tab as well
        sid = f"saved:{key}"
        if sid in self._saved_rows and self._stree.exists(sid):
            color_s, label_s = STATUS_COLORS.get(status, (TEXT3, "● UNKNOWN"))
            tag_s = STATUS_TAGS.get(status, "s_offline")
            self._stree.item(sid, tags=(tag_s,))
            self._stree.set(sid, "status", label_s)
            if status == ModelStatus.RECORDING:
                fname = os.path.basename(detail) if detail else "…"
                self._stree.set(sid, "file", fname)
            elif status in (ModelStatus.ONLINE, ModelStatus.OFFLINE,
                            ModelStatus.CHECKING, ModelStatus.PRIVATE):
                self._stree.set(sid, "file", "—")
                self._stree.set(sid, "size", "—")
            if self._status_filter_set("saved") is not None:
                self._schedule_filter("saved")  # status changed → re-filter

        if key not in self._rows or not self._tree.exists(key):
            return

        # Guard: don't let a stale ONLINE callback overwrite an active recording
        if status == ModelStatus.ONLINE:
            with self.recorder._lock:
                cfg = self.recorder.models.get(key)
            if cfg and cfg.session:
                # Model is actually recording — ignore this late ONLINE callback
                return

        color, label = STATUS_COLORS.get(status, (TEXT3, "● UNKNOWN"))
        tag = STATUS_TAGS.get(status, "s_offline")
        self._tree.item(key, tags=(tag,))
        self._tree.set(key, "status", label)

        if status == ModelStatus.RECORDING:
            fname = os.path.basename(detail) if detail else "…"
            self._tree.set(key, "file", fname)
        elif status == ModelStatus.ONLINE:
            self._tree.set(key, "file", "—")
            self._tree.set(key, "size", "—")
            self._size_cache.pop(key, None)
            # Auto-record when the Recorder monitor is active and AUTO is checked
            if self._monitoring_recorder and self._auto_rec.get(key, False):
                site, name = key.split(":", 1)
                def _do(n=name, s=site):
                    self.recorder.start_recording(n, s)
                threading.Thread(target=_do, daemon=True).start()
        elif status in (ModelStatus.OFFLINE, ModelStatus.CHECKING, ModelStatus.PRIVATE):
            self._tree.set(key, "file", "—")
            self._tree.set(key, "size", "—")
            self._size_cache.pop(key, None)
        elif status == ModelStatus.ERROR:
            self._tree.set(key, "file", detail[:50] if detail else "error")

        # Coalesced: _stats_tick recomputes at most twice a second instead of
        # one full row scan per status callback.
        self._stats_dirty = True
        if self._status_filter_set("rec") is not None:
            self._schedule_filter("rec")  # status changed → re-filter (debounced)

    def _bw_tick(self):
        """Update the header bandwidth meter once a second from the relay's
        upstream byte counter (covers all relay-routed recordings)."""
        try:
            import cb_relay
            total = cb_relay.bytes_downloaded()
        except Exception:
            total = 0
        now = time.monotonic()
        if self._bw_prev is not None:
            t0, b0 = self._bw_prev
            dt = max(now - t0, 0.001)
            cur = (total - b0) * 8 / dt / 1_000_000
            # light smoothing so the number doesn't flicker
            self._bw_mbps = 0.6 * self._bw_mbps + 0.4 * cur
        self._bw_prev = (now, total)
        mbps = self._bw_mbps
        if mbps >= 0.05:
            self._lbl_bw.configure(text=f"↓ {mbps:.1f} Mbps", fg=GREEN)
        else:
            self._lbl_bw.configure(text="↓ 0.0 Mbps", fg=TEXT3)

        # Upload: pipeline → Telegram
        try:
            ul_total = self.pipeline.bytes_uploaded() if self.pipeline else 0
        except Exception:
            ul_total = 0
        if self._ul_prev is not None:
            t0, b0 = self._ul_prev
            dt = max(now - t0, 0.001)
            # counter resets when the pipeline restarts → ignore negative deltas
            cur = max(0.0, (ul_total - b0) * 8 / dt / 1_000_000)
            # TDLib's uploaded_size jumps to the full file size instantly when
            # Telegram dedupes a file it already has or resumes a partial
            # upload — not real wire traffic. Drop physically implausible
            # samples instead of showing a multi-hundred-Mbps spike.
            if cur <= 500.0:
                self._ul_mbps = 0.6 * self._ul_mbps + 0.4 * cur
        self._ul_prev = (now, ul_total)
        if self._ul_mbps >= 0.05:
            self._lbl_bw_up.configure(text=f"↑ {self._ul_mbps:.1f} Mbps", fg=ORANGE)
        else:
            self._lbl_bw_up.configure(text="↑ 0.0 Mbps", fg=TEXT3)
        self.after(1000, self._bw_tick)

    def _cb_log(self, line: str):
        # Called from worker threads — just queue; _drain_logs batches the
        # Tk work (deque.append is thread-safe, no Tk call needed here).
        self._log_queue.append(("app", line, "info"))

    def _drain_logs(self):
        try:
            if self._log_queue:
                app_lines, pipe_lines = [], []
                while self._log_queue:
                    dest, msg, tag = self._log_queue.popleft()
                    (app_lines if dest == "app" else pipe_lines).append((msg, tag))
                if app_lines:
                    self._bulk_insert(self._log, app_lines, 2000)
                if pipe_lines:
                    self._bulk_insert(self._pipe_log, pipe_lines, 1000)
        finally:
            self.after(250, self._drain_logs)

    def _bulk_insert(self, widget, lines, max_lines: int):
        widget.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        for msg, tag in lines:
            line = msg if msg.startswith("[") else f"[{ts}]  {msg}"
            widget.insert("end", line + "\n", tag)
        n = int(widget.index("end-1c").split(".")[0])
        if n > max_lines:
            widget.delete("1.0", f"{n - max_lines}.0")
        # Autoscroll only when someone can actually see it — see() forces
        # layout work that's wasted while the tab is hidden or the privacy
        # cover is up.
        if self._privacy_canvas is None and widget.winfo_viewable():
            widget.see("end")
        widget.configure(state="disabled")

    def _cb_notif(self, title: str, body: str):
        if self.settings.notifications_enabled and self._v_notif.get():
            send_notification(title, body)

    # ── Saved Models tab ─────────────────────────────────────────────────────

    def _build_saved_tab(self, p):
        bar = tk.Frame(p, bg=BG2, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="Saved Models  ·  view-only status watchlist",
                 fg=TEXT3, bg=BG2, font=("Segoe UI", 9)).pack(side="left", padx=12)
        self._lbl_saved_count = tk.Label(bar, text="0 model(s)", fg=TEXT2,
                                         bg=BG2, font=("Segoe UI Semibold", 9))
        self._lbl_saved_count.pack(side="left", padx=(0, 8))
        self._lbl_saved_count.bind("<Button-1>",
                                   lambda e: self._uncheck_all("saved"))
        tk.Label(bar, text="🔎", fg=TEXT3, bg=BG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._v_filter_saved = tk.StringVar()
        ttk.Entry(bar, textvariable=self._v_filter_saved, width=14
                  ).pack(side="left", padx=(2, 0), pady=8)
        self._v_filter_saved.trace_add(
            "write", lambda *a: self._schedule_filter("saved"))
        self._mb_status_saved = self._build_status_menubutton(bar, "saved")
        self._mb_status_saved.pack(side="left", padx=(4, 0), pady=5)
        self._btn_monitor_saved = ttk.Button(bar, text="▶  START SCANNER",
                                              style="Green.TButton",
                                              command=self._toggle_monitor_saved)
        self._btn_monitor_saved.pack(side="right", padx=10, pady=5)
        ttk.Button(bar, text="＋ Add Current Recorder Model",
                   style="Flat.TButton",
                   command=self._saved_add_prompt).pack(side="right", padx=6, pady=5)
        ttk.Button(bar, text="📥 Import", style="Flat.TButton",
                   command=self._saved_import).pack(side="right", padx=2, pady=5)
        ttk.Button(bar, text="📤 Export", style="Flat.TButton",
                   command=self._saved_export).pack(side="right", padx=2, pady=5)

        cols = ("rank", "status", "file", "size")
        frame = tk.Frame(p, bg=BG)
        frame.pack(fill="both", expand=True)

        self._stree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                    selectmode="extended", style="Dark.Treeview")
        self._stree_sort_reverse: dict[str, bool] = {}
        self._stree.heading("#0", text="MODEL  ↕", anchor="w",
                            command=lambda: self._sort_stree("#0"))
        self._stree.column("#0", width=200, minwidth=120)
        self._stree.heading("rank", text="RANK  ↕", anchor="w",
                            command=lambda: self._sort_stree("rank"))
        self._stree.column("rank", width=92, minwidth=92, stretch=False)
        self._stree.heading("status", text="STATUS  ↕", anchor="w",
                            command=lambda: self._sort_stree("status"))
        self._stree.column("status", width=140, minwidth=80)
        self._stree.heading("file", text="CURRENT FILE", anchor="w")
        self._stree.column("file", width=320, minwidth=100)
        self._stree.heading("size", text="SIZE  ↕", anchor="e",
                            command=lambda: self._sort_stree("size"))
        self._stree.column("size", width=90, minwidth=60, anchor="e")

        sb = ttk.Scrollbar(frame, orient="vertical", command=self._stree.yview)
        self._stree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._stree.pack(side="left", fill="both", expand=True)

        for tag, color in [("s_offline", TEXT3), ("s_online", GREEN),
                            ("s_recording", ACCENT), ("s_error", RED),
                            ("s_checking", YELLOW)]:
            self._stree.tag_configure(tag, foreground=color)
        self._stree.tag_configure("site_hdr", foreground=ACCENT,
                                   font=("Segoe UI Semibold", 9))

        self._stree.bind("<Button-1>", self._on_stree_click)
        self._stree.bind("<B1-Motion>", lambda e: self._on_tree_drag(e, "saved"))
        self._stree.bind("<ButtonRelease-1>",
                         lambda e: self._end_tree_drag("saved"), add="+")
        self._stree.bind("<Button-3>", self._on_stree_right_click)
        self._stree.bind("<Control-a>", lambda e: self._select_all(self._stree))

        self._sctx = tk.Menu(self, tearoff=0, bg=BG3, fg=TEXT,
                              activebackground=BG2, activeforeground=ACCENT,
                              font=UI, relief="flat", bd=0)

        # Restore persisted saved models as DATA only — rows are built on the
        # first tab visit (_populate_saved_tab), engine registration happens
        # at scanner start (_register_saved_models).
        for m in getattr(self.settings, "saved_models", []) or []:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self._saved_data[self._saved_key(n, s)] = {"name": n, "site": s}
                # Migrate a rank stored inline on older configs into the
                # shared store (settings.ranks is authoritative now).
                r = int(m.get("rank", 0) or 0)
                if r:
                    self._ranks.setdefault(self._rank_key(n, s), r)
        self._update_saved_count()

    def _populate_saved_tab(self):
        """Build the Treeview rows for the saved watchlist on first use, then
        sync each row's status from the recorder — a model that is already
        recording in the Recorder tab shows RECORDING here immediately, and
        live mirroring (_apply_status) takes over once the rows exist."""
        if self._saved_built:
            return
        self._saved_built = True
        for d in self._saved_data.values():
            # _insert_saved_model skips existing rows and ends with
            # _saved_sync_from_recorder (status/file mirror)
            self._insert_saved_model(d["name"], d["site"])
        self._update_saved_count()
        if (self._v_filter_saved.get().strip()
                or self._status_filter_set("saved") is not None):
            self._filter_saved()

    def _register_saved_models(self):
        """Register the watchlist in the recording engine — needed only when
        the scanner actually runs. add_model is idempotent: models that are
        also in the Recorder tab just gain the 'saved' group."""
        for d in self._saved_data.values():
            self.recorder.add_model(d["name"], d["site"], "saved", quiet=True)
        if self._saved_data:
            self._log_add(f"Registered {len(self._saved_data)} saved model(s) "
                          f"for scanning.")

    def _update_saved_count(self, visible: Optional[int] = None):
        if visible is not None:
            self._saved_visible = visible  # remember across check toggles
        total = len(self._saved_data)
        vis = getattr(self, "_saved_visible", None)
        if vis is not None and vis < total:
            txt = f"{vis} / {total} shown"
        else:
            txt = f"{total} model(s)"
        if self._saved_checked:
            txt += f"  ·  ✓ {len(self._saved_checked)}"
        self._lbl_saved_count.configure(text=txt)

    # ── Tree filtering ────────────────────────────────────────────────────────

    _SITE_ORDER = ("chaturbate", "stripchat", "camsoda", "myfreecams")
    _FILTER_STATUSES = (ModelStatus.ONLINE, ModelStatus.RECORDING,
                        ModelStatus.OFFLINE, ModelStatus.PRIVATE,
                        ModelStatus.CHECKING, ModelStatus.ERROR)

    def _build_status_menubutton(self, bar, which: str) -> ttk.Menubutton:
        """'Status ▾' dropdown with one checkbox per status — any combination
        (e.g. Online + Recording). Nothing checked = show all."""
        svars = {st: tk.BooleanVar(value=False) for st in self._FILTER_STATUSES}
        if which == "rec":
            self._status_vars_rec = svars
        else:
            self._status_vars_saved = svars
        mb = ttk.Menubutton(bar, text="Status: All", style="TMenubutton")
        menu = tk.Menu(mb, tearoff=0, bg=BG3, fg=TEXT, activebackground=BG2,
                       activeforeground=ACCENT, font=UI, relief="flat", bd=0)
        for st, var in svars.items():
            menu.add_checkbutton(label=st.value.title(), variable=var,
                                 command=lambda w=which: self._on_status_filter(w))
        mb["menu"] = menu
        return mb

    def _status_filter_set(self, which: str) -> Optional[set]:
        """Checked statuses, or None when the filter is inactive (none or all
        checked — both mean 'show everything')."""
        svars = getattr(self, "_status_vars_rec" if which == "rec"
                        else "_status_vars_saved", None)
        if not svars:
            return None
        sel = {st for st, v in svars.items() if v.get()}
        return sel if sel and len(sel) < len(svars) else None

    def _on_status_filter(self, which: str):
        sel = self._status_filter_set(which)
        mb = self._mb_status_rec if which == "rec" else self._mb_status_saved
        mb.configure(text="Status: All" if sel is None else f"Status: {len(sel)}")
        self._schedule_filter(which)

    def _on_tab_changed(self, event=None):
        if self._nb.select() == str(self._tab_saved):
            self._populate_saved_tab()

    def _schedule_filter(self, which: str):
        """Debounce filter keystrokes — a full pass over 1500 rows per
        keypress is wasted work while the user is still typing."""
        job = self._filter_jobs.get(which)
        if job:
            self.after_cancel(job)
        fn = self._filter_recorder if which == "rec" else self._filter_saved
        self._filter_jobs[which] = self.after(250, fn)

    def _filter_recorder(self):
        self._filter_jobs["rec"] = None
        self._filter_tree(self._tree, list(self._rows),
                          self._v_filter_rec.get(), "_site_",
                          self._status_filter_set("rec"))

    def _filter_saved(self):
        self._filter_jobs["saved"] = None
        if not self._saved_built:
            return  # rows not built yet — _populate_saved_tab applies it
        visible = self._filter_tree(self._stree, list(self._saved_rows),
                                    self._v_filter_saved.get(), "_ssite_",
                                    self._status_filter_set("saved"))
        self._update_saved_count(visible)

    def _filter_tree(self, tree, row_iids, query, hdr_prefix,
                     statuses: Optional[set] = None) -> int:
        """Show only rows whose model name contains `query` AND (when a
        status set is given) whose status is in it; the rest are detached
        (hidden, not deleted — values keep updating and reattach when the
        filter clears). Site headers with no visible rows are hidden too.
        Returns the number of visible rows."""
        q = query.strip().lower()
        sites_seen: list[str] = []
        sites_visible: set[str] = set()
        shown = 0
        for iid in row_iids:
            parts = iid.split(":")
            site, name = parts[-2], parts[-1]
            hdr = f"{hdr_prefix}{site}"
            if site not in sites_seen:
                sites_seen.append(site)
            if not tree.exists(iid) or not tree.exists(hdr):
                continue
            ok = not q or q in name.lower()
            if ok and statuses is not None:
                ok = STATUS_BY_LABEL.get(tree.set(iid, "status")) in statuses
            if ok:
                tree.move(iid, hdr, "end")
                sites_visible.add(site)
                shown += 1
            else:
                tree.selection_remove(iid)  # never leave hidden rows selected
                tree.detach(iid)
        order = [s for s in self._SITE_ORDER if s in sites_seen] + \
                [s for s in sites_seen if s not in self._SITE_ORDER]
        for idx, site in enumerate(order):
            hdr = f"{hdr_prefix}{site}"
            if not tree.exists(hdr):
                continue
            if site in sites_visible:
                tree.move(hdr, "", idx)
            else:
                tree.detach(hdr)
        return shown

    def _saved_ensure_site(self, site: str):
        site_id = f"_ssite_{site}"
        if self._stree.exists(site_id):
            return
        label = {"chaturbate": "CHATURBATE", "stripchat": "STRIPCHAT",
                 "camsoda": "CAMSODA",
                 "myfreecams": "MYFREECAMS"}.get(site, site.upper())
        self._stree.insert("", "end", iid=site_id, text=f"  {label}",
                           values=("", "", "", ""), tags=("site_hdr",), open=True)

    def _saved_key(self, name: str, site: str) -> str:
        return f"saved:{site}:{name.lower()}"

    @staticmethod
    def _rank_stars(rank: int) -> str:
        """Render a 0-5 rank as five star glyphs (filled + empty), e.g.
        3 → '★★★☆☆'. Always five glyphs so an unranked row is still
        clickable star-by-star."""
        r = max(0, min(5, int(rank or 0)))
        return "★" * r + "☆" * (5 - r)

    @staticmethod
    def _rank_from_stars(text: str) -> int:
        return text.count("★")

    @staticmethod
    def _rank_key(name: str, site: str) -> str:
        return f"{site.lower()}:{name.lower()}"

    def _get_rank(self, name: str, site: str) -> int:
        return int(self._ranks.get(self._rank_key(name, site), 0) or 0)

    def _set_rank_many(self, items, rank: int) -> int:
        """Set the 0-5 star rank on a list of (name, site) models — the single
        place ranks change. Updates the shared store, refreshes the matching
        rows in BOTH trees (whichever exist), and persists once. Returns the
        number of models whose rank actually changed."""
        rank = max(0, min(5, int(rank)))
        stars = self._rank_stars(rank)
        changed = 0
        for name, site in items:
            k = self._rank_key(name, site)
            if int(self._ranks.get(k, 0) or 0) != rank:
                changed += 1
            if rank:
                self._ranks[k] = rank
            else:
                self._ranks.pop(k, None)
            rkey = f"{site}:{name.lower()}"
            if self._tree.exists(rkey):
                self._tree.set(rkey, "rank", stars)
            sid = self._saved_key(name, site)
            if self._stree.exists(sid):
                self._stree.set(sid, "rank", stars)
        if changed:
            self._persist_models()
        return changed

    def _saved_rank(self, sid: str) -> int:
        _, site, name = sid.split(":", 2)
        return self._get_rank(name, site)

    def _set_saved_rank(self, sids, rank: int) -> int:
        """Saved-tab entry point: rank one or many rows (by sid)."""
        if isinstance(sids, str):
            sids = [sids]
        items = []
        for sid in sids:
            if sid in self._saved_data or self._stree.exists(sid):
                _, site, name = sid.split(":", 2)
                items.append((name, site))
        return self._set_rank_many(items, rank)

    def _insert_saved_model(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        sid = self._saved_key(name, site)
        if sid in self._saved_rows:
            return
        self._saved_ensure_site(site)
        parent = f"_ssite_{site}"
        self._stree.insert(parent, "end", iid=sid, text=f"☐  {name}",
                           values=(self._rank_stars(self._saved_rank(sid)),
                                   "●  OFFLINE", "—", "—"),
                           tags=("s_offline",))
        self._saved_rows[sid] = True
        # Refresh initial size/status
        self._saved_sync_from_recorder(name, site)
        if self._saved_built and (self._v_filter_saved.get().strip()
                                  or self._status_filter_set("saved") is not None):
            self._schedule_filter("saved")  # respect an active filter

    def _saved_sync_from_recorder(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        sid = self._saved_key(name, site)
        if not self._stree.exists(sid):
            return
        cfg = self.recorder.models.get(key)
        if not cfg:
            return
        color, label = STATUS_COLORS.get(cfg.status, (TEXT3, "● UNKNOWN"))
        tag = STATUS_TAGS.get(cfg.status, "s_offline")
        self._stree.item(sid, tags=(tag,))
        self._stree.set(sid, "status", label)
        if cfg.session and cfg.session.current_file:
            self._stree.set(sid, "file", os.path.basename(cfg.session.current_file))

    def _add_to_saved(self, name: str, site: str):
        sid = self._saved_key(name, site)
        if sid in self._saved_data:
            messagebox.showerror("Already saved",
                                 f"{name} ({site}) is already in Saved Models.")
            return
        self._saved_data[sid] = {"name": name, "site": site}
        # Engine registration is only needed while the scanner runs; otherwise
        # it happens in bulk at scanner start.
        if self._monitoring_saved:
            self.recorder.add_model(name, site, "saved")
        self._insert_saved_model(name, site)
        self._update_saved_count()
        self._persist_models()
        self._log_add(f"⭐  Added to Saved Models: {name} ({site})", "accent")
        key = f"{site}:{name.lower()}"
        if self._tree.exists(key):
            self._tree.set(key, "saved", "✔️")
        self._update_saved_btn()

    def _add_to_recorder(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        if key in self._rows:
            messagebox.showerror("Already in Recorder",
                                 f"{name} ({site}) is already in the Recorder list.")
            return
        self.recorder.add_model(name, site, "recorder")
        self._insert_model(name, site)
        self._persist_models()
        self._update_stats()
        self._log_add(f"Added to Recorder: {name} ({site})", "accent")

    def _remove_saved(self, sid: str, persist: bool = True):
        # The row may not exist yet (lazy tab) — the data entry is what counts
        if sid not in self._saved_data and not self._stree.exists(sid):
            return
        self._saved_data.pop(sid, None)
        _, site, name = sid.split(":", 2)
        if self._stree.exists(sid):
            self._stree.delete(sid)
        self._saved_rows.pop(sid, None)
        self._saved_checked.discard(sid)
        site_id = f"_ssite_{site}"
        # _saved_rows includes filter-detached rows; get_children() doesn't
        if (self._stree.exists(site_id)
                and not any(s.split(":")[1] == site for s in self._saved_rows)):
            self._stree.delete(site_id)
        self.recorder.remove_model(name, site, "saved")
        rec_key = f"{site}:{name}"
        if self._tree.exists(rec_key):
            self._tree.set(rec_key, "saved", "❌")
        if persist:  # bulk callers persist/log once at the end instead
            self._update_saved_count()
            self._persist_models()
            self._log_add(f"Removed from Saved Models: {name} ({site})", "warn")
            self._update_saved_btn()

    def _remove_saved_many(self, sids: list):
        for sid in list(sids):
            self._remove_saved(sid, persist=False)
        self._update_saved_count()
        self._persist_models()
        self._update_saved_btn()
        self._log_add(f"Removed {len(sids)} model(s) from Saved Models", "warn")

    def _add_many_to_recorder(self, sids: list):
        added = 0
        for sid in sids:
            _, site, name = sid.split(":", 2)
            key = f"{site}:{name}"
            if key in self._rows:
                continue  # already in the Recorder tab
            self.recorder.add_model(name, site, "recorder", quiet=True)
            self._insert_model(name, site)
            added += 1
        if added:
            self._persist_models()
            self._update_stats()
        self._log_add(f"Added {added} model(s) to Recorder"
                      + (f" ({len(sids) - added} already there)"
                         if added < len(sids) else ""), "accent")

    def _saved_targets(self, clicked: Optional[str] = None) -> list:
        """Rows a saved-tab bulk action operates on: checked boxes first,
        then the multi-selection, then just the clicked row."""
        if self._saved_checked:
            return [s for s in self._saved_checked if self._stree.exists(s)]
        sel = [s for s in self._stree.selection() if not s.startswith("_ssite_")]
        if sel:
            return sel
        return [clicked] if clicked else []

    def _on_stree_click(self, event):
        """Toggle the row checkbox in the Saved tab."""
        region = self._stree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        col = self._stree.identify_column(event.x)
        sid = self._stree.identify_row(event.y)
        if not sid or sid.startswith("_ssite_"):
            return
        modified = event.state & 0x0005  # Shift / Control held
        if col == "#0" and event.x <= 48 and not modified:
            self._toggle_check(self._stree, sid, self._saved_checked)
            return "break"
        # Click a star in the RANK column to set 1-5 stars; clicking the
        # star that's already the rank clears it back to 0.
        if col == "#1" and not modified:
            bbox = self._stree.bbox(sid, col)
            if bbox:
                bx, _, bw, _ = bbox
                star = int((event.x - bx) / (bw / 5)) + 1
                star = max(1, min(5, star))
                self._set_saved_rank(sid, 0 if self._saved_rank(sid) == star
                                     else star)
            return "break"
        if not modified:
            self._drag_anchor["saved"] = sid

    def _on_stree_right_click(self, event):
        sid = self._stree.identify_row(event.y)
        if not sid or sid.startswith("_ssite_"):
            return
        if sid not in self._stree.selection():
            self._stree.selection_set(sid)
        targets = self._saved_targets(sid)
        n = len(targets)
        m = self._sctx
        m.delete(0, "end")
        if n == 1:
            _, site, name = targets[0].split(":", 2)
            m.add_command(label=f"＋  Add to Recorder  {name}",
                          command=lambda: self._add_to_recorder(name, site))
            m.add_command(label="🔗  Copy Model URL",
                          command=lambda: self._copy_model_url(name, site))
            m.add_command(label="🌐  Open in Browser",
                          command=lambda t=targets: self._open_in_browser(t, saved=True))
            m.add_separator()
            m.add_command(label="✕  Remove from Saved Models",
                          command=lambda t=targets[0]: self._remove_saved(t))
        else:
            src = "checked" if self._saved_checked else "selected"
            m.add_command(label=f"＋  Add to Recorder  ({n} {src})",
                          command=lambda t=targets: self._add_many_to_recorder(t))
            m.add_command(label=f"🌐  Open in Browser  ({n} {src})",
                          command=lambda t=targets: self._open_in_browser(t, saved=True))
            m.add_command(label=f"📋  Copy as OneTab List  ({n} {src})",
                          command=lambda t=targets: self._copy_onetab(t, saved=True))
            m.add_separator()
            m.add_command(label=f"✕  Remove from Saved  ({n} {src})",
                          command=lambda t=targets: self._remove_saved_many(t))
        # Set Rank submenu — works for one row or the whole selection/check set
        m.add_separator()
        rank_lbl = "Set Rank" if n == 1 else f"Set Rank  ({n})"
        rank_menu = tk.Menu(m, tearoff=0, bg=BG3, fg=TEXT,
                            activebackground=BG2, activeforeground=ACCENT,
                            font=UI, relief="flat", bd=0)
        for r in (5, 4, 3, 2, 1):
            rank_menu.add_command(
                label=self._rank_stars(r),
                command=lambda t=targets, rr=r: self._set_saved_rank(t, rr))
        rank_menu.add_separator()
        rank_menu.add_command(
            label="☆  Clear rank",
            command=lambda t=targets: self._set_saved_rank(t, 0))
        m.add_cascade(label=f"⭐  {rank_lbl}", menu=rank_menu)
        m.add_separator()
        raw_sel = [s for s in self._stree.selection()
                   if not s.startswith("_ssite_")]
        if raw_sel:
            m.add_command(label=f"☑  Check Selected  ({len(raw_sel)})",
                          command=lambda: self._check_selection("saved"))
        m.add_command(label="☑  Check All Visible",
                      command=lambda: self._check_all_visible("saved"))
        if self._saved_checked:
            m.add_command(label=f"☐  Uncheck All  ({len(self._saved_checked)})",
                          command=lambda: self._uncheck_all("saved"))
        m.tk_popup(event.x_root, event.y_root)

    def _saved_add_prompt(self):
        # Quick helper: pop a small dialog to add a username/URL into Saved Models
        from tkinter import simpledialog
        raw = simpledialog.askstring("Add to Saved Models",
                                      "Username or URL (chaturbate / stripchat / camsoda / myfreecams):",
                                      parent=self)
        if not raw:
            return
        name, site = self._parse_model_input(raw)
        if not name:
            messagebox.showwarning("Input required", "Could not parse a username.")
            return
        self._add_to_saved(name, site)

    def _saved_export(self):
        if not self._saved_data:
            messagebox.showinfo("Nothing to export", "Saved Models list is empty.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Saved Models",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialfile="saved_models.json",
        )
        if not path:
            return
        # Merge into an existing export rather than overwriting it: entries
        # already in the file are kept, ranks are refreshed from the current
        # list, and models the file has but we don't are preserved untouched.
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
                pass  # unreadable/garbage file — fall back to a clean write
        updated = 0
        for d in self._saved_data.values():
            k = f"{d['site'].lower()}:{d['name'].lower()}"
            if k in merged:
                updated += 1
            merged[k] = {"name": d["name"], "site": d["site"],
                         "rank": self._get_rank(d["name"], d["site"])}
        items = list(merged.values())
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "saved_models": items}, f, indent=2)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        kept = existing - updated  # file-only entries left untouched
        self._log_add(f"Exported {len(items)} saved model(s) → {path}", "success")
        messagebox.showinfo(
            "Export complete",
            f"Wrote {len(items)} model(s) to:\n{path}" +
            (f"\n\nMerged with existing file: {kept} kept, "
             f"{updated} updated." if existing else ""))

    def _saved_import(self):
        path = filedialog.askopenfilename(
            title="Import Saved Models",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Import failed", f"Could not read file:\n{e}")
            return
        items = data.get("saved_models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            messagebox.showerror("Import failed",
                                 "File is not a valid saved-models export.")
            return
        added = skipped = invalid = ranked = 0
        for m in items:
            if not isinstance(m, dict):
                invalid += 1
                continue
            n, s = (m.get("name") or "").strip().lower(), (m.get("site") or "").strip().lower()
            if not n or not s:
                invalid += 1
                continue
            rank = max(0, min(5, int(m.get("rank", 0) or 0)))
            sid = self._saved_key(n, s)
            rkey = self._rank_key(n, s)
            if sid in self._saved_data:
                # Already on the watchlist — still pull in a rank if the file
                # carries one and ours is unset, so importing a ranked export
                # back-fills stars onto existing models.
                if rank and self._get_rank(n, s) == 0:
                    self._ranks[rkey] = rank
                    if self._stree.exists(sid):
                        self._stree.set(sid, "rank", self._rank_stars(rank))
                    ranked += 1
                else:
                    skipped += 1
                continue
            self._saved_data[sid] = {"name": n, "site": s}
            if rank:
                self._ranks[rkey] = rank
            if self._monitoring_saved:
                self.recorder.add_model(n, s, "saved", quiet=True)
            self._insert_saved_model(n, s)
            added += 1
        if added or ranked:
            self._update_saved_count()
            self._persist_models()
        self._log_add(
            f"Import: added {added}, ranked {ranked}, "
            f"skipped {skipped} duplicate(s)" +
            (f", {invalid} invalid" if invalid else ""),
            "success" if (added or ranked) else "warn",
        )
        messagebox.showinfo(
            "Import complete",
            f"Added: {added}\nRanks applied to existing: {ranked}\n"
            f"Skipped (already present): {skipped}" +
            (f"\nInvalid entries: {invalid}" if invalid else ""),
        )

    # ── Output / Upload tab (Pipeline) ───────────────────────────────────────

    def _build_output_tab(self, p):
        from pipeline_worker import PipelineWorker, PipelineConfig
        self._PipelineWorker = PipelineWorker
        self._PipelineConfig = PipelineConfig
        self.pipeline: Optional[PipelineWorker] = None

        # Top control bar
        bar = tk.Frame(p, bg=BG2, height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._btn_pipeline = ttk.Button(bar, text="▶  START PIPELINE",
            style="Green.TButton", command=self._toggle_pipeline)
        self._btn_pipeline.pack(side="left", padx=12, pady=8)

        self._lbl_pipe_state = tk.Label(bar, text="● STOPPED",
            fg=TEXT3, bg=BG2, font=("Segoe UI Semibold", 10))
        self._lbl_pipe_state.pack(side="left", padx=8)

        ttk.Button(bar, text="🔑  Re-auth / Switch Account",
            style="Flat.TButton",
            command=self._pipeline_reauth).pack(side="right", padx=6, pady=8)

        # Settings grid
        cfg_frame = tk.LabelFrame(p, text=" Telegram / Pipeline settings ",
            bg=BG, fg=TEXT2, font=("Segoe UI Semibold", 9),
            relief="flat", bd=0, padx=10, pady=8)
        cfg_frame.pack(fill="x", padx=12, pady=(8, 4))

        self._pipe_vars = {}
        fields = [
            ("telegram_api_id",      "API ID",            30),
            ("telegram_api_hash",    "API Hash",          50),
            ("telegram_group_id",    "Chat / Group ID",   30),
            ("_topic_id",            "Topic ID (0 if none)", 10),
            ("pipeline_converted_dir", "Converted .mp4 folder", 60),
            ("telegram_session_dir", "TDLib session folder", 60),
        ]
        for i, (key, label, width) in enumerate(fields):
            r, c = divmod(i, 2)
            cell = tk.Frame(cfg_frame, bg=BG)
            cell.grid(row=r, column=c, sticky="ew", padx=6, pady=3)
            cfg_frame.columnconfigure(c, weight=1)
            tk.Label(cell, text=label, fg=TEXT3, bg=BG,
                     font=("Segoe UI", 9)).pack(anchor="w")
            var = tk.StringVar()
            if key == "_topic_id":
                var.set(str(getattr(self.settings, "telegram_topic_id", "") or "1"))
            elif key == "pipeline_converted_dir":
                var.set(getattr(self.settings, "pipeline_converted_dir", "") or "")
            elif key == "telegram_session_dir":
                var.set(getattr(self.settings, "telegram_session_dir", "") or "")
            else:
                var.set(str(getattr(self.settings, key, "") or ""))
            e = ttk.Entry(cell, textvariable=var, width=width)
            e.pack(fill="x")
            self._pipe_vars[key] = var

        ttk.Button(cfg_frame, text="💾  Save Pipeline Settings",
                   style="Flat.TButton",
                   command=self._save_pipeline_settings).grid(
            row=99, column=0, columnspan=2, sticky="e", pady=(8, 0))

        # Progress / status area
        prog = tk.Frame(p, bg=BG2)
        prog.pack(fill="x", padx=12, pady=6)
        self._pipe_convert_lbl = tk.Label(prog, text="Convert:  idle",
            fg=YELLOW, bg=BG2, font=MONO, anchor="w")
        self._pipe_convert_lbl.pack(fill="x", padx=10, pady=(6, 2))
        self._pipe_upload_lbls: list = []
        for i in range(2):
            lbl = tk.Label(prog, text=f"Upload {i+1}: idle",
                fg=GREEN, bg=BG2, font=MONO, anchor="w")
            lbl.pack(fill="x", padx=10, pady=(0, 2))
            self._pipe_upload_lbls.append(lbl)

        # Pipeline log
        logf = tk.Frame(p, bg=BG)
        logf.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self._pipe_log = tk.Text(logf, bg=BG, fg=TEXT2, font=MONO,
            relief="flat", bd=0, padx=10, pady=6,
            state="disabled", wrap="word")
        ls = ttk.Scrollbar(logf, command=self._pipe_log.yview)
        self._pipe_log.configure(yscrollcommand=ls.set)
        ls.pack(side="right", fill="y")
        self._pipe_log.pack(fill="both", expand=True)

    def _save_pipeline_settings(self):
        s = self.settings
        s.telegram_api_id   = self._pipe_vars["telegram_api_id"].get().strip()
        s.telegram_api_hash = self._pipe_vars["telegram_api_hash"].get().strip()
        s.telegram_group_id = self._pipe_vars["telegram_group_id"].get().strip()
        s.telegram_topic_id = self._pipe_vars["_topic_id"].get().strip()
        s.pipeline_converted_dir = self._pipe_vars["pipeline_converted_dir"].get().strip()
        s.telegram_session_dir   = self._pipe_vars["telegram_session_dir"].get().strip()
        save_pipeline_settings(s)
        self._pipe_log_add("Pipeline settings saved.", "success")

    def _build_pipeline_config(self):
        cfg = self._PipelineConfig()
        s = self.settings
        try:
            cfg.api_id = int(self._pipe_vars["telegram_api_id"].get().strip() or "0")
        except ValueError:
            cfg.api_id = 0
        cfg.api_hash = self._pipe_vars["telegram_api_hash"].get().strip()
        cfg.phone = ""  # phone only needed for first-time auth; left blank reuses session
        try:
            cfg.chat_id = int(self._pipe_vars["telegram_group_id"].get().strip() or "0")
        except ValueError:
            cfg.chat_id = 0
        try:
            cfg.topic_id = int(self._pipe_vars["_topic_id"].get().strip() or "0")
        except ValueError:
            cfg.topic_id = 0
        cfg.watch_folder = s.output_dir
        cfg.output_folder = (self._pipe_vars["pipeline_converted_dir"].get().strip()
                             or os.path.join(s.output_dir, "converted"))
        cfg.tdlib_dir = (self._pipe_vars["telegram_session_dir"].get().strip()
                         or os.path.join(os.path.dirname(__file__), "Pipeline", ".tdlib"))
        cfg.uploaded_log = os.path.join(cfg.tdlib_dir, "uploaded.txt")
        # ffmpeg bundled with the app
        cfg.ffmpeg_path = self.recorder.ffmpeg_path or "ffmpeg"
        cfg.ffprobe_path = "ffprobe"
        return cfg

    def _toggle_pipeline(self, silent: bool = False):
        if self.pipeline and self.pipeline.running:
            self.pipeline.stop()
            self._btn_pipeline.configure(text="▶  START PIPELINE", style="Green.TButton")
            self._lbl_pipe_state.configure(text="● STOPPING", fg=ORANGE)
            return

        cfg = self._build_pipeline_config()
        missing = []
        if not cfg.api_id:   missing.append("API ID")
        if not cfg.api_hash: missing.append("API Hash")
        if not cfg.chat_id:  missing.append("Chat ID")
        if missing:
            # silent=True path is used by the API (no modal dialog on the UI
            # thread — that would freeze the app that serves the API).
            if silent:
                self._log_add("Pipeline start (API) failed — missing settings: "
                              + ", ".join(missing), "error")
            else:
                messagebox.showerror("Missing settings",
                    "Please fill in: " + ", ".join(missing))
            return

        os.makedirs(cfg.output_folder, exist_ok=True)
        os.makedirs(cfg.tdlib_dir, exist_ok=True)

        self.pipeline = self._PipelineWorker(
            cfg,
            on_log=lambda line: self._log_queue.append(("pipe", line, "info")),
            on_state=lambda st: self.after(0, lambda: self._pipeline_state_changed(st)),
            on_progress=lambda k, n, p, s=0.0: self.after(0,
                lambda: self._pipeline_progress(k, n, p, s)),
            prompt_cb=self._pipeline_prompt,
        )
        self.pipeline.start()
        self._btn_pipeline.configure(text="⏹  STOP PIPELINE", style="Red.TButton")
        self._lbl_pipe_state.configure(text="● STARTING", fg=YELLOW)
        self._pipe_log_add("Starting pipeline...", "accent")

    def _pipeline_reauth(self):
        import shutil
        dir_ = (self._pipe_vars["telegram_session_dir"].get().strip()
                or os.path.join(os.path.dirname(__file__), "Pipeline", ".tdlib"))
        if not os.path.isdir(dir_):
            messagebox.showinfo("Re-auth",
                "No TDLib session folder found — next Start will begin a fresh login.")
            return
        if not messagebox.askyesno("Re-auth",
                f"Delete cached Telegram session in:\n{dir_}\n\nYou'll need to log in again on next Start."):
            return
        try:
            shutil.rmtree(dir_)
            self._pipe_log_add(f"Cleared session at {dir_}.", "warn")
        except Exception as e:
            messagebox.showerror("Re-auth failed", str(e))

    def _pipeline_state_changed(self, state: str):
        colors = {
            "starting":  (YELLOW, "● STARTING"),
            "running":   (GREEN,  "● RUNNING"),
            "stopping":  (ORANGE, "● STOPPING"),
            "stopped":   (TEXT3,  "● STOPPED"),
            "error":     (RED,    "● ERROR"),
        }
        c, label = colors.get(state, (TEXT3, f"● {state.upper()}"))
        self._lbl_pipe_state.configure(text=label, fg=c)
        if state in ("stopped", "error"):
            self._btn_pipeline.configure(text="▶  START PIPELINE", style="Green.TButton")
            self._pipe_convert_lbl.configure(text="Convert:  idle")
            for i, lbl in enumerate(self._pipe_upload_lbls):
                lbl.configure(text=f"Upload {i+1}: idle")

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps <= 0:
            return ""
        if bps >= 1_048_576:
            return f"  {bps/1_048_576:.1f} MB/s"
        return f"  {bps/1024:.0f} KB/s"

    def _pipeline_progress(self, kind: str, name: str, pct: float, speed: float = 0.0):
        bar_len = 22
        filled = int(round(bar_len * pct / 100.0))
        bar = "█" * filled + "░" * (bar_len - filled)
        short_name = name[:34]
        spd = self._fmt_speed(speed)
        if kind == "convert":
            idle = pct >= 100.0 and not name
            txt = ("Convert:  idle" if idle
                   else f"Convert:  [{bar}] {pct:5.1f}%  {short_name}")
            self._pipe_convert_lbl.configure(text=txt)
        elif kind.startswith("upload"):
            slot = int(kind.replace("upload", "") or "1") - 1
            slot = max(0, min(slot, len(self._pipe_upload_lbls) - 1))
            idle = pct >= 100.0 and not name
            prefix = f"Upload {slot+1}:"
            txt = (f"{prefix} idle" if idle
                   else f"{prefix} [{bar}] {pct:5.1f}%{spd}  {short_name}")
            self._pipe_upload_lbls[slot].configure(text=txt)

    def _pipeline_prompt(self, label: str) -> str:
        """Called from worker thread — must block until GUI dialog returns."""
        from tkinter import simpledialog
        result = {"val": ""}
        done = threading.Event()

        def _ask():
            try:
                val = simpledialog.askstring("Telegram", label,
                                              parent=self, show="•")
                result["val"] = val or ""
            finally:
                done.set()

        self.after(0, _ask)
        done.wait(timeout=300)
        return result["val"]

    def _pipe_log_add(self, msg: str, tag: str = "info"):
        self._pipe_log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._pipe_log.insert("end", f"[{ts}]  {msg}\n")
        self._pipe_log.see("end")
        # Cap at 1000 lines
        n = int(self._pipe_log.index("end-1c").split(".")[0])
        if n > 1000:
            self._pipe_log.delete("1.0", f"{n - 1000}.0")
        self._pipe_log.configure(state="disabled")

    # ── Log tab ───────────────────────────────────────────────────────────────

    def _build_log_tab(self, p):
        bar = tk.Frame(p, bg=BG2, height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="Activity Log", fg=TEXT2, bg=BG2,
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=12)
        ttk.Button(bar, text="Clear", style="Flat.TButton",
                   command=self._clear_log).pack(side="right", padx=8, pady=4)

        f = tk.Frame(p, bg=BG)
        f.pack(fill="both", expand=True)
        self._log = tk.Text(f, bg=BG, fg=TEXT2, font=MONO,
                             relief="flat", bd=0, padx=12, pady=8,
                             state="disabled", wrap="word")
        ls = ttk.Scrollbar(f, command=self._log.yview)
        self._log.configure(yscrollcommand=ls.set)
        ls.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True)
        for tag, color in [("info", TEXT2), ("success", GREEN), ("warn", ORANGE),
                            ("error", RED), ("accent", ACCENT)]:
            self._log.tag_configure(tag, foreground=color)

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log_add(self, msg: str, tag: str = "info"):
        self._log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        # avoid double-timestamp if msg already has one
        line = msg if msg.startswith("[") else f"[{ts}]  {msg}"
        self._log.insert("end", line + "\n", tag)
        # Cap log at 2000 lines to prevent unbounded memory growth
        line_count = int(self._log.index("end-1c").split(".")[0])
        if line_count > 2000:
            self._log.delete("1.0", f"{line_count - 2000}.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # ── Settings ──────────────────────────────────────────────────────────────

    def _pick_folder(self):
        f = filedialog.askdirectory(initialdir=self._e_folder.get())
        if f:
            self._e_folder.delete(0, "end")
            self._e_folder.insert(0, f)

    def _save_settings(self):
        self.settings.output_dir            = self._e_folder.get().strip()
        self.settings.max_size_mb           = self._parse_int(self._v_maxsize.get())
        self.settings.check_interval        = self._parse_int(self._v_interval.get(), 30)
        self.settings.minimize_to_tray       = self._v_tray.get()
        self.settings.notifications_enabled = self._v_notif.get()
        self.settings.gap_warnings_enabled  = self._v_gapwarn.get()
        self.settings.max_quality = QUALITY_OPTIONS.get(self._v_quality.get(), 0)
        self.settings.auto_downgrade_enabled = self._v_autodown.get()
        self.settings.playwright_fallback_enabled = self._v_pwfallback.get()
        self.recorder.quality_global = self.settings.max_quality
        self.recorder.auto_downgrade_enabled = self.settings.auto_downgrade_enabled
        self.recorder.playwright_fallback_enabled = self.settings.playwright_fallback_enabled
        self.settings.privacy_mode_enabled  = self._v_privacy.get()
        self.recorder.gap_warnings_enabled  = self.settings.gap_warnings_enabled
        self._persist_models()
        save_settings(self.settings)
        self.recorder.output_dir     = self.settings.output_dir
        self.recorder.max_size_mb    = self.settings.max_size_mb
        self.recorder.check_interval = self.settings.check_interval
        os.makedirs(self.settings.output_dir, exist_ok=True)
        if _is_cloud_synced(self.settings.output_dir):
            self._log_add("⚠ Output folder is inside a cloud-synced directory "
                          "(OneDrive/Dropbox) — a local folder is strongly "
                          "recommended.", "warn")
        self._log_add("Settings saved.", "success")

    def _persist_models(self):
        self.settings.models = [
            {
                "name": k.split(":")[1],
                "site": k.split(":")[0],
                "auto_rec": self._auto_rec.get(k, False),
                "max_q": self._model_q.get(k, 0),
            }
            for k in self._rows
        ]
        # From _saved_data (source of truth), NOT the UI rows — with the lazy
        # tab the rows may not exist yet, and persisting from them would wipe
        # the whole watchlist on the first save.
        self.settings.saved_models = [
            {"name": d["name"], "site": d["site"]}
            for d in self._saved_data.values()
        ]
        # Ranks are keyed by model identity and persisted on their own so a
        # rank survives even if the model is in neither list (e.g. ranked from
        # the extension). Drop zero entries to keep the file tidy.
        self.settings.ranks = {k: v for k, v in self._ranks.items() if v}
        save_settings(self.settings)

    def _restore_models(self):
        for m in self.settings.models:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self.recorder.add_model(n, s, "recorder")
                self._insert_model(n, s, auto_rec=bool(m.get("auto_rec", False)))
                q = int(m.get("max_q", 0) or 0)
                if q:
                    self._model_q[f"{s}:{n}"] = q
                r = int(m.get("rank", 0) or 0)  # migrate older inline ranks
                if r:
                    self._ranks.setdefault(self._rank_key(n, s), r)
        self._update_stats()

    # Dashboard rows — emoji colors match each site's brand
    # (CB yellow, SC red, CS blue, MFC green). ▶ recording · ● online · ○ offline.
    _DASH_SITES = [
        ("chaturbate", "🟡", "CB"),
        ("stripchat",  "🔴", "SC"),
        ("camsoda",    "🔵", "CS"),
        ("myfreecams", "🟢", "MFC"),
    ]

    def _dashboard_counts(self):
        """Per-site [total, recording, online, offline] + grand totals.
        Snapshots the row keys so it's safe to call from the API handler
        thread (same read-only pattern as the /status endpoint)."""
        tally = {s: [0, 0, 0, 0] for s, _e, _l in self._DASH_SITES}
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
            else:                       # offline/error/checking/private → offline
                t[3] += 1
        total = [0, 0, 0, 0]
        for c in tally.values():
            for i in range(4):
                total[i] += c[i]
        return tally, total

    def _build_stats_panel(self, p):
        """Compact color-coded status grid: a '● N LIVE' hero (recording count
        in red) over per-site rows. Built once; _update_stats only re-sets text
        and color in place, so live status flips don't flicker the panel."""
        wrap = tk.Frame(p, bg=BG2)
        wrap.pack(anchor="w", fill="x", padx=16, pady=8)

        hero = tk.Frame(wrap, bg=BG2)
        hero.pack(fill="x")
        self._lbl_live = tk.Label(hero, text="● 0 LIVE", fg=ACCENT, bg=BG2,
                                  font=("Consolas", 13, "bold"), anchor="w")
        self._lbl_live.pack(side="left")
        self._lbl_total = tk.Label(hero, text="0 models", fg=TEXT3, bg=BG2,
                                   font=("Consolas", 9), anchor="e")
        self._lbl_total.pack(side="right")

        grid = tk.Frame(wrap, bg=BG2)
        grid.pack(fill="x", pady=(6, 0))
        grid.grid_columnconfigure(0, weight=1)
        for col in (1, 2, 3):
            grid.grid_columnconfigure(col, minsize=30, uniform="num")

        # Header glyph row doubles as the color legend: ▶ red · ● green · ○ grey.
        for col, (txt, fg) in enumerate(
                (("", TEXT3), ("▶", ACCENT), ("●", GREEN), ("○", TEXT3))):
            tk.Label(grid, text=txt, fg=fg, bg=BG2, font=("Consolas", 9),
                     anchor="e" if col else "w").grid(
                row=0, column=col, sticky="e" if col else "w", pady=(0, 2))

        self._stat_cells = {}
        row = 1
        for site, emoji, lbl in self._DASH_SITES:
            code = tk.Label(grid, text=f"{emoji} {lbl}", fg=TEXT, bg=BG2,
                            font=("Consolas", 10), anchor="w")
            nums = [tk.Label(grid, text="0", fg=TEXT3, bg=BG2,
                             font=("Consolas", 10), anchor="e")
                    for _ in range(3)]
            code.grid(row=row, column=0, sticky="w")
            for i, n in enumerate(nums):
                n.grid(row=row, column=i + 1, sticky="e")
            self._stat_cells[site] = (code, *nums)
            row += 1

        tk.Frame(grid, bg=BORDER, height=1).grid(
            row=row, column=0, columnspan=4, sticky="ew", pady=4)
        row += 1

        tk.Label(grid, text="ALL", fg=TEXT2, bg=BG2,
                 font=("Consolas", 10, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w")
        self._tot_cells = [tk.Label(grid, text="0", bg=BG2,
                           font=("Consolas", 10, "bold"), anchor="e")
                           for _ in range(3)]
        for i, n in enumerate(self._tot_cells):
            n.grid(row=row, column=i + 1, sticky="e")

    def _update_stats(self):
        tally, total = self._dashboard_counts()
        self._lbl_live.configure(text=f"● {total[1]} LIVE")
        self._lbl_total.configure(text=f"{total[0]} models")
        palette = (ACCENT, GREEN, TEXT2)            # recording, online, offline
        for site, _e, _l in self._DASH_SITES:
            code, *nums = self._stat_cells[site]
            c = tally[site]
            if c[0]:
                code.grid()
                for i, n in enumerate(nums):
                    v = c[i + 1]
                    n.configure(text=str(v), fg=palette[i] if v else TEXT3)
                    n.grid()
            else:
                code.grid_remove()
                for n in nums:
                    n.grid_remove()
        for i, n in enumerate(self._tot_cells):
            v = total[i + 1]
            n.configure(text=str(v), fg=palette[i] if v else TEXT3)

    def _api_dashboard(self) -> dict:
        """Aggregate dashboard snapshot for the bot/API (GET /dashboard)."""
        tally, total = self._dashboard_counts()
        keys = ("total", "recording", "online", "offline")
        sites = {lbl: dict(zip(keys, tally[s]))
                 for s, _e, lbl in self._DASH_SITES}
        return {"ok": True, "sites": sites, "all": dict(zip(keys, total))}

    @staticmethod
    def _parse_int(val: str, default: int = None) -> Optional[int]:
        try:
            return int(val.strip()) if val.strip() else default
        except ValueError:
            return default

    # ── Close ─────────────────────────────────────────────────────────────────

    def _start_api_server(self):
        _ApiHandler._app = self
        try:
            self._api_server = HTTPServer(("127.0.0.1", _API_PORT), _ApiHandler)
            t = threading.Thread(target=self._api_server.serve_forever,
                                 daemon=True, name="api-server")
            t.start()
        except OSError as e:
            # Almost always "port already in use" — i.e. another Scr33nX is
            # already running and owns the API port. Make it loud instead of
            # silently starting a second, half-working instance whose tray icon
            # and browser extension talk to the OTHER process.
            self._api_server = None
            logging.getLogger().warning(
                "API server could not bind 127.0.0.1:%s (%s) — is another "
                "Scr33nX already running? The browser extension will talk to "
                "that instance, not this one.", _API_PORT, e)
            self._log_add(
                f"⚠  Local API port {_API_PORT} is already in use — another "
                f"Scr33nX is likely running. The browser extension is "
                f"controlling that instance, not this window.", "warn")

    def _stop_api_server(self):
        if getattr(self, "_api_server", None):
            self._api_server.shutdown()

    # ── System tray ───────────────────────────────────────────────────────────

    def _on_window_unmap(self, event):
        if event.widget is not self:
            return
        if self._hiding_to_tray:
            return
        if not self._v_tray.get():
            return
        if self.state() != "iconic":
            return
        self.after_idle(self._minimize_to_tray)

    def _minimize_to_tray(self):
        if WinTray is None or not self._v_tray.get():
            return
        self._hiding_to_tray = True
        try:
            self.withdraw()
            self._ensure_tray()
        finally:
            self._hiding_to_tray = False

    def _ensure_tray(self):
        if WinTray is None:
            return
        if self._tray is None:
            self._tray = WinTray(
                "Scr33nX",
                on_show=self._tray_show_evt.set,
                on_quit=self._tray_quit_evt.set,
            )
            # Asynchronous — failure is picked up by _poll_tray, so the Tk
            # thread never blocks waiting for the tray thread.
            self._tray.add()
        if self._tray_poll_id is None:
            self._tray_poll_id = self.after(150, self._poll_tray)

    def _poll_tray(self):
        self._tray_poll_id = None
        if self._tray is not None and self._tray.failed():
            # Tray icon couldn't be created — bring the window back
            self._remove_tray()
            self.deiconify()
            return
        if self._tray_quit_evt.is_set():
            self._tray_quit_evt.clear()
            self._on_close()
            return
        if self._tray_show_evt.is_set():
            self._tray_show_evt.clear()
            self._do_restore_from_tray()
        if self._tray is not None:
            self._tray_poll_id = self.after(150, self._poll_tray)

    def _do_restore_from_tray(self):
        # Window was withdrawn while iconic — force normal state first so
        # deiconify doesn't bring it back minimized.
        try:
            self.state("normal")
        except tk.TclError:
            pass
        self.deiconify()
        self.lift()
        # Briefly toggle topmost so Windows actually brings us to the front.
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _remove_tray(self):
        if self._tray_poll_id is not None:
            try:
                self.after_cancel(self._tray_poll_id)
            except Exception:
                pass
            self._tray_poll_id = None
        if self._tray:
            try:
                self._tray.remove()
            except Exception:
                pass
            self._tray = None

    def _on_close(self):
        if getattr(self, "_closing", False):
            return
        recording = any(
            self.recorder.models.get(k) and
            self.recorder.models[k].status == ModelStatus.RECORDING
            for k in self._rows
        )
        if recording:
            # Quitting from the tray menu: the window is withdrawn, so the
            # confirm dialog needs a visible parent first.
            if self.state() == "withdrawn":
                self._do_restore_from_tray()
            if not messagebox.askyesno("Quit", "Recordings are active. Stop and exit?"):
                return
        self._closing = True
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._lbl_hdr_status.configure(text="● STOPPING…", fg=ORANGE)
        self._stop_api_server()
        self._remove_tray()
        self._save_settings()

        # Flushing every active ffmpeg takes up to ~20 s with many
        # recordings — run it off the Tk thread so the window doesn't
        # freeze, then destroy from the Tk thread.
        def _shutdown():
            try:
                self.recorder.stop_monitor()
            except Exception:
                pass
            if hasattr(self, "pipeline"):
                try:
                    self.pipeline.stop()
                except Exception:
                    pass
            try:
                self.after(0, self.destroy)
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=_shutdown, daemon=True, name="shutdown").start()


if __name__ == "__main__":
    try:
        StreamRecorderApp().mainloop()
    except Exception:
        # pythonw discards stderr — without this, a startup crash is an
        # invisible flash. Log it and tell the user where to look.
        logging.getLogger().exception("Fatal error — app crashed")
        try:
            _root = tk.Tk(); _root.withdraw()
            messagebox.showerror(
                "Scr33nX crashed",
                "A fatal error occurred.\n\nDetails: streamrecorder.log in "
                "%LOCALAPPDATA%\\Scr33nX")
            _root.destroy()
        except Exception:
            pass
        raise
