"""
app.py — WebcamRecorder 0.10 RC — GUI
"""

import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from recorder import StreamRecorder, ModelStatus
from settings import AppSettings, load_settings, save_settings, save_pipeline_settings
from notifier import send_notification

if sys.platform == "win32":
    from tray_win import WinTray
else:
    WinTray = None  # type: ignore

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = "#0d0f12"
BG2     = "#13161b"
BG3     = "#1a1e26"
BORDER  = "#252b36"
ACCENT  = "#00d4ff"
ACCENT2 = "#0099bb"
GREEN   = "#00e676"
RED     = "#ff3d57"
ORANGE  = "#ffab40"
YELLOW  = "#ffd740"
TEXT    = "#e8ecf0"
TEXT2   = "#7a8494"
TEXT3   = "#4a5260"
MONO    = ("Consolas", 10)
UI      = ("Segoe UI", 10)

STATUS_COLORS = {
    ModelStatus.OFFLINE:   (TEXT3,  "●  OFFLINE"),
    ModelStatus.ONLINE:    (GREEN,  "●  ONLINE"),
    ModelStatus.RECORDING: (ACCENT, "⬤  RECORDING"),
    ModelStatus.ERROR:     (RED,    "✖  ERROR"),
    ModelStatus.CHECKING:  (YELLOW, "◌  CHECKING..."),
    ModelStatus.PRIVATE:   (YELLOW, "🔒  PRIVATE / TICKET"),
}

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
            "in_saved":    sid in app._saved_rows,
            "status":      status_str,
            "auto":        app._auto_rec.get(key, False),
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
        if site not in ("chaturbate", "stripchat", "camsoda"):
            self._json({"ok": False, "error": f"unsupported site: {site}"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        sid = f"saved:{key}"
        # Pre-check so we return a useful error before scheduling
        if target == "saved" and sid in app._saved_rows:
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
        if target != "recorder":
            self._json({"ok": False, "error": f"unsupported target: {target}"}, 400)
            return
        app = self._app
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
        self.title("WebcamRecorder 0.10 Release Candidate")
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

        self._rows: dict[str, bool] = {}          # key → exists flag
        self._saved_rows: dict[str, bool] = {}    # saved-only models (view only)
        self._auto_rec: dict[str, bool] = {}      # key → auto-rec state (recorder tab)
        self._monitoring_recorder = False
        self._monitoring_saved    = False
        self._tray: Optional[WinTray] = None
        self._hiding_to_tray = False
        self._build_styles()
        self._build_ui()
        self._restore_models()
        self._start_api_server()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if WinTray is not None:
            self.bind("<Unmap>", self._on_window_unmap)

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
            ("Green.TButton",  GREEN,  BG,    "#00b35a"),
            ("Red.TButton",    RED,    TEXT,  "#cc2e42"),
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
        tk.Label(hdr, text="⬤", fg=ACCENT, bg=BG2,
                 font=("Segoe UI", 17)).pack(side="left", padx=(16,4))
        tk.Label(hdr, text="STREAM", fg=TEXT, bg=BG2,
                 font=("Segoe UI Black", 15)).pack(side="left")
        tk.Label(hdr, text="RECORDER", fg=ACCENT, bg=BG2,
                 font=("Segoe UI Black", 15)).pack(side="left", padx=(2,0))
        self._lbl_hdr_status = tk.Label(hdr, text="● IDLE", fg=TEXT3, bg=BG2,
                                         font=("Segoe UI Semibold", 10))
        self._lbl_hdr_status.pack(side="right", padx=12)

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
        self._c_site = ttk.Combobox(p, values=["chaturbate","stripchat","camsoda"],
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

        self._v_tray = tk.BooleanVar(value=self.settings.minimize_to_tray)
        tk.Checkbutton(p, text="Minimize to SysTray", variable=self._v_tray,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,4))

        self._v_notif = tk.BooleanVar(value=self.settings.notifications_enabled)
        tk.Checkbutton(p, text="Notifications", variable=self._v_notif,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,4))

        ttk.Button(p, text="💾  Save Settings", style="Flat.TButton",
                   command=self._save_settings).pack(fill="x", padx=16, pady=(0,0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(16,0))
        self._lbl_stats = tk.Label(p, text="0 recording  ·  0 offline",
                                    fg=TEXT3, bg=BG2, font=("Segoe UI", 9))
        self._lbl_stats.pack(anchor="w", padx=16, pady=8)

    def _build_right(self, p):
        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True)

        # Recorder tab
        tab_m = ttk.Frame(nb)
        nb.add(tab_m, text="  Recorder  ")
        self._build_models_tab(tab_m)

        # Saved Models tab
        tab_s = ttk.Frame(nb)
        nb.add(tab_s, text="  Saved Models  ")
        self._build_saved_tab(tab_s)

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

        self._btn_monitor_rec = ttk.Button(bar, text="▶  START MONITOR",
                                            style="Green.TButton",
                                            command=self._toggle_monitor_recorder)
        self._btn_monitor_rec.pack(side="right", padx=10, pady=5)

        # ── Treeview ──
        cols = ("status", "file", "size", "auto", "saved")
        frame = tk.Frame(p, bg=BG)
        frame.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                   selectmode="extended", style="Dark.Treeview")

        # Tree column (#0) = model name — sortable
        self._tree.heading("#0", text="MODEL  ↕", anchor="w",
                           command=lambda: self._sort_tree("#0"))
        self._tree.column("#0", width=200, minwidth=120)

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
                 "camsoda": "CAMSODA"}.get(site, site.upper())
        self._tree.insert("", "end", iid=site_id, text=f"  {label}",
                          values=("", "", "", ""), tags=("site_hdr",), open=True)

    def _update_selection_label(self):
        sel = self._get_selected_keys()
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
        if self._saved_key(name, site) in self._saved_rows:
            self._btn_saved.configure(text="★ Remove from Saved")
        else:
            self._btn_saved.configure(text="★ Add to Saved")

    def _toggle_saved_selected(self):
        """Add or remove selected model(s) from Saved Models."""
        keys = self._get_selected_keys()
        if not keys:
            return
        site, name = keys[0].split(":", 1)
        if self._saved_key(name, site) in self._saved_rows:
            for key in keys:
                s, n = key.split(":", 1)
                self._remove_saved(self._saved_key(n, s))
        else:
            for key in keys:
                s, n = key.split(":", 1)
                self._add_to_saved(n, s)

    def _get_selected_keys(self) -> list[str]:
        """Return selected model keys (skip site headers)."""
        return [iid for iid in self._tree.selection()
                if not iid.startswith("_site_")]

    def _on_tree_click(self, event):
        """Toggle AUTO when clicking the auto column."""
        region = self._tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        col = self._tree.identify_column(event.x)
        iid = self._tree.identify_row(event.y)
        if not iid or iid.startswith("_site_"):
            return
        # col "#5" = the 4th data column = "auto" (since #0 is tree column,
        # #1=status, #2=file, #3=size, #4=auto)
        if col == "#4":
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
                          command=lambda: self.recorder.stop_recording(name, site))
            m.add_separator()
            auto = self._auto_rec.get(iid, False)
            m.add_command(label=f"{'☑' if auto else '☐'}  Auto-Record",
                          command=lambda: self._toggle_auto_single(iid))
            sid_check = self._saved_key(name, site)
            if sid_check in self._saved_rows:
                m.add_command(label="✕  Remove from Saved Models",
                              command=lambda s=sid_check: self._remove_saved(s))
            else:
                m.add_command(label="⭐  Add to Saved Models",
                              command=lambda: self._add_to_saved(name, site))
            m.add_separator()
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
            m.add_separator()
            m.add_command(label="📁  Open Output Folder",
                          command=lambda: os.startfile(self.settings.output_dir))
            m.add_command(label=f"✕  Remove  ({n} selected)",
                          command=self._remove_selected)

        m.tk_popup(event.x_root, event.y_root)

    def _toggle_auto_single(self, key: str):
        self._set_auto(key, not self._auto_rec.get(key, False))

    def _set_auto(self, key: str, val: bool):
        self._auto_rec[key] = val
        self._tree.set(key, "auto", "☑" if val else "☐")
        self._persist_models()

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
                children.sort(
                    key=lambda iid: tree.item(iid, "text").strip().lower(),
                    reverse=reverse)
            elif col == "size":
                children.sort(
                    key=lambda iid: self._size_sort_key(tree.set(iid, "size")),
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
            col_labels={"#0": "MODEL", "status": "STATUS",
                        "size": "SIZE", "auto": "AUTO", "saved": "SAVED"},
        )

    def _sort_stree(self, col: str):
        self._sort_generic(
            self._stree, col,
            site_prefix="_ssite_",
            sort_state=self._stree_sort_reverse,
            col_labels={"#0": "MODEL", "status": "STATUS", "size": "SIZE"},
        )

    # ── Model management ─────────────────────────────────────────────────────

    def _parse_model_input(self, raw: str) -> tuple[str, str]:
        """Parse a username or URL into (name, site).
        Supports:
          chaturbate.com/username/
          stripchat.com/username/
          camsoda.com/username/
          plain username (uses the site dropdown)
        """
        raw = raw.strip().lower()
        # Strip protocol
        for prefix in ("https://", "http://", "www."):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]

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
        saved_text = "✔️" if self._saved_key(name, site) in self._saved_rows else "❌"
        self._tree.insert(parent, "end", iid=key, text=f"  {name}",
                          values=("●  OFFLINE", "—", "—", auto_text, saved_text),
                          tags=("s_offline",))
        self._rows[key] = True
        self._auto_rec[key] = auto_rec
        self._poll_size(key)

    def _do_remove_from_recorder(self, name: str, site: str):
        """Remove a model from the Recorder tab without a confirm dialog.
        Shared by the right-click flow (after confirm) and the extension API."""
        key = f"{site}:{name}"
        self.recorder.remove_model(name, site, "recorder")
        if self._tree.exists(key):
            self._tree.delete(key)
        self._rows.pop(key, None)
        self._auto_rec.pop(key, None)
        site_id = f"_site_{site}"
        if self._tree.exists(site_id) and not self._tree.get_children(site_id):
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
            site_id = f"_site_{site}"
            if self._tree.exists(site_id) and not self._tree.get_children(site_id):
                self._tree.delete(site_id)
            self._log_add(f"Removed: {name} ({site})", "warn")
        self._persist_models()
        self._update_stats()
        self._update_selection_label()

    def _stop_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            site, name = key.split(":", 1)
            cfg = self.recorder.models.get(key)
            if cfg and cfg.status == ModelStatus.RECORDING:
                self.recorder.stop_recording(name, site)
                self._log_add(f"Stopped recording: {name} ({site})", "warn")

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

    def _poll_size(self, key: str):
        # Stop polling if model is in neither list
        in_rec = key in self._rows and self._tree.exists(key)
        sid = f"saved:{key}"
        in_saved = sid in self._saved_rows and self._stree.exists(sid)
        if not in_rec and not in_saved:
            return
        cfg = self.recorder.models.get(key)
        if cfg and cfg.session and cfg.session.current_file:
            try:
                mb = os.path.getsize(cfg.session.current_file) / (1024*1024)
                txt = f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"
                if in_rec:
                    self._tree.set(key, "size", txt)
                if in_saved:
                    self._stree.set(sid, "size", txt)
            except OSError:
                pass
        self.after(3000, lambda k=key: self._poll_size(k))

    # ── Record toggle (per model) ─────────────────────────────────────────────

    def _toggle_rec(self, key: str, name: str, site: str):
        with self.recorder._lock:
            cfg = self.recorder.models.get(key)
        if not cfg:
            return
        if cfg.status == ModelStatus.RECORDING:
            self.recorder.stop_recording(name, site)
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
            self.recorder.stop_monitor("recorder")
            self._monitoring_recorder = False
            self._btn_monitor_rec.configure(text="▶  START MONITOR", style="Green.TButton")
            self._log_add("Recorder monitor stopped.", "warn")
        else:
            self._sync_recorder_settings()
            self.recorder.start_monitor("recorder")
            self._monitoring_recorder = True
            self._btn_monitor_rec.configure(text="⏹  STOP MONITOR", style="Red.TButton")
            self._log_add("Recorder monitor started.", "success")
        self._refresh_hdr_status()

    def _toggle_monitor_saved(self):
        if self._monitoring_saved:
            self.recorder.stop_monitor("saved")
            self._monitoring_saved = False
            self._btn_monitor_saved.configure(text="▶  START SCANNER", style="Green.TButton")
            self._log_add("Saved Models scanner stopped.", "warn")
        else:
            self._sync_recorder_settings()
            self.recorder.start_monitor("saved")
            self._monitoring_saved = True
            self._btn_monitor_saved.configure(text="⏹  STOP SCANNER", style="Red.TButton")
            self._log_add("Saved Models scanner started.", "success")
        self._refresh_hdr_status()

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
            # Auto-record when the Recorder monitor is active and AUTO is checked
            if self._monitoring_recorder and self._auto_rec.get(key, False):
                site, name = key.split(":", 1)
                def _do(n=name, s=site):
                    self.recorder.start_recording(n, s)
                threading.Thread(target=_do, daemon=True).start()
        elif status in (ModelStatus.OFFLINE, ModelStatus.CHECKING, ModelStatus.PRIVATE):
            self._tree.set(key, "file", "—")
            self._tree.set(key, "size", "—")
        elif status == ModelStatus.ERROR:
            self._tree.set(key, "file", detail[:50] if detail else "error")

        self._update_stats()

    def _cb_log(self, line: str):
        self.after(0, lambda: self._log_add(line))

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

        cols = ("status", "file", "size")
        frame = tk.Frame(p, bg=BG)
        frame.pack(fill="both", expand=True)

        self._stree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                    selectmode="extended", style="Dark.Treeview")
        self._stree_sort_reverse: dict[str, bool] = {}
        self._stree.heading("#0", text="MODEL  ↕", anchor="w",
                            command=lambda: self._sort_stree("#0"))
        self._stree.column("#0", width=200, minwidth=120)
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

        self._stree.bind("<Button-3>", self._on_stree_right_click)
        self._stree.bind("<Control-a>", lambda e: self._select_all(self._stree))

        self._sctx = tk.Menu(self, tearoff=0, bg=BG3, fg=TEXT,
                              activebackground=BG2, activeforeground=ACCENT,
                              font=UI, relief="flat", bd=0)

        # Restore persisted saved models
        for m in getattr(self.settings, "saved_models", []) or []:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self.recorder.add_model(n, s, "saved")
                self._insert_saved_model(n, s)

    def _saved_ensure_site(self, site: str):
        site_id = f"_ssite_{site}"
        if self._stree.exists(site_id):
            return
        label = {"chaturbate": "CHATURBATE", "stripchat": "STRIPCHAT",
                 "camsoda": "CAMSODA"}.get(site, site.upper())
        self._stree.insert("", "end", iid=site_id, text=f"  {label}",
                           values=("", "", ""), tags=("site_hdr",), open=True)

    def _saved_key(self, name: str, site: str) -> str:
        return f"saved:{site}:{name.lower()}"

    def _insert_saved_model(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        sid = self._saved_key(name, site)
        if sid in self._saved_rows:
            return
        self._saved_ensure_site(site)
        parent = f"_ssite_{site}"
        self._stree.insert(parent, "end", iid=sid, text=f"  {name}",
                           values=("●  OFFLINE", "—", "—"),
                           tags=("s_offline",))
        self._saved_rows[sid] = True
        # Refresh initial size/status
        self._saved_sync_from_recorder(name, site)

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
        if sid in self._saved_rows:
            messagebox.showerror("Already saved",
                                 f"{name} ({site}) is already in Saved Models.")
            return
        self.recorder.add_model(name, site, "saved")
        self._insert_saved_model(name, site)
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

    def _remove_saved(self, sid: str):
        if not self._stree.exists(sid):
            return
        _, site, name = sid.split(":", 2)
        self._stree.delete(sid)
        self._saved_rows.pop(sid, None)
        site_id = f"_ssite_{site}"
        if self._stree.exists(site_id) and not self._stree.get_children(site_id):
            self._stree.delete(site_id)
        self.recorder.remove_model(name, site, "saved")
        rec_key = f"{site}:{name}"
        if self._tree.exists(rec_key):
            self._tree.set(rec_key, "saved", "❌")
        self._persist_models()
        self._log_add(f"Removed from Saved Models: {name} ({site})", "warn")
        self._update_saved_btn()

    def _on_stree_right_click(self, event):
        sid = self._stree.identify_row(event.y)
        if not sid or sid.startswith("_ssite_"):
            return
        if sid not in self._stree.selection():
            self._stree.selection_set(sid)
        _, site, name = sid.split(":", 2)
        m = self._sctx
        m.delete(0, "end")
        m.add_command(label=f"＋  Add to Recorder  {name}",
                      command=lambda: self._add_to_recorder(name, site))
        m.add_separator()
        m.add_command(label="✕  Remove from Saved Models",
                      command=lambda: self._remove_saved(sid))
        m.tk_popup(event.x_root, event.y_root)

    def _saved_add_prompt(self):
        # Quick helper: pop a small dialog to add a username/URL into Saved Models
        from tkinter import simpledialog
        raw = simpledialog.askstring("Add to Saved Models",
                                      "Username or URL (chaturbate / stripchat / camsoda):",
                                      parent=self)
        if not raw:
            return
        name, site = self._parse_model_input(raw)
        if not name:
            messagebox.showwarning("Input required", "Could not parse a username.")
            return
        self._add_to_saved(name, site)

    def _saved_export(self):
        if not self._saved_rows:
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
        items = []
        for sid in self._saved_rows:
            # sid format: "saved:<site>:<name>"
            _, site, name = sid.split(":", 2)
            items.append({"name": name, "site": site})
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "saved_models": items}, f, indent=2)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        self._log_add(f"Exported {len(items)} saved model(s) → {path}", "success")
        messagebox.showinfo("Export complete",
                            f"Exported {len(items)} model(s) to:\n{path}")

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
        added = skipped = invalid = 0
        for m in items:
            if not isinstance(m, dict):
                invalid += 1
                continue
            n, s = (m.get("name") or "").strip().lower(), (m.get("site") or "").strip().lower()
            if not n or not s:
                invalid += 1
                continue
            if self._saved_key(n, s) in self._saved_rows:
                skipped += 1
                continue
            self.recorder.add_model(n, s, "saved")
            self._insert_saved_model(n, s)
            added += 1
        if added:
            self._persist_models()
        self._log_add(
            f"Import: added {added}, skipped {skipped} duplicate(s)" +
            (f", {invalid} invalid" if invalid else ""),
            "success" if added else "warn",
        )
        messagebox.showinfo(
            "Import complete",
            f"Added: {added}\nSkipped (already present): {skipped}" +
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

    def _toggle_pipeline(self):
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
            messagebox.showerror("Missing settings",
                "Please fill in: " + ", ".join(missing))
            return

        os.makedirs(cfg.output_folder, exist_ok=True)
        os.makedirs(cfg.tdlib_dir, exist_ok=True)

        self.pipeline = self._PipelineWorker(
            cfg,
            on_log=lambda line: self.after(0, lambda: self._pipe_log_add(line)),
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
        self._persist_models()
        save_settings(self.settings)
        self.recorder.output_dir     = self.settings.output_dir
        self.recorder.max_size_mb    = self.settings.max_size_mb
        self.recorder.check_interval = self.settings.check_interval
        os.makedirs(self.settings.output_dir, exist_ok=True)
        self._log_add("Settings saved.", "success")

    def _persist_models(self):
        self.settings.models = [
            {
                "name": k.split(":")[1],
                "site": k.split(":")[0],
                "auto_rec": self._auto_rec.get(k, False),
            }
            for k in self._rows
        ]
        self.settings.saved_models = [
            {
                "name": sid.split(":")[2],
                "site": sid.split(":")[1],
            }
            for sid in self._saved_rows
        ]
        save_settings(self.settings)

    def _restore_models(self):
        for m in self.settings.models:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self.recorder.add_model(n, s, "recorder")
                self._insert_model(n, s, auto_rec=bool(m.get("auto_rec", False)))
        self._update_stats()

    def _update_stats(self):
        recording = 0
        online    = 0
        offline   = 0
        for k in self._rows:
            cfg = self.recorder.models.get(k)
            if cfg:
                if cfg.status == ModelStatus.RECORDING:
                    recording += 1
                elif cfg.status == ModelStatus.ONLINE:
                    online += 1
                else:
                    offline += 1
            else:
                offline += 1
        parts = []
        if recording: parts.append(f"{recording} recording")
        if online:    parts.append(f"{online} online")
        if offline:   parts.append(f"{offline} offline")
        self._lbl_stats.configure(text="  ·  ".join(parts) if parts else "0 offline")

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
        except OSError:
            self._api_server = None

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
            hwnd = self.winfo_id()
            self._tray = WinTray(
                hwnd,
                "WebcamRecorder",
                on_show=self._restore_from_tray,
                on_quit=self._on_close,
            )
            try:
                self._tray.add()
            except OSError:
                self._tray = None
                self.deiconify()
                return

    def _restore_from_tray(self):
        self.after(0, self._do_restore_from_tray)

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
        if self._tray:
            try:
                self._tray.remove()
            except Exception:
                pass
            self._tray = None

    def _on_close(self):
        recording = any(
            self.recorder.models.get(k) and
            self.recorder.models[k].status == ModelStatus.RECORDING
            for k in self._rows
        )
        if recording:
            if not messagebox.askyesno("Quit", "Recordings are active. Stop and exit?"):
                return
        self.recorder.stop_monitor()
        if hasattr(self, "pipeline"):
            try:
                self.pipeline.stop()
            except Exception:
                pass
        self._stop_api_server()
        self._remove_tray()
        self._save_settings()
        self.destroy()


if __name__ == "__main__":
    StreamRecorderApp().mainloop()
