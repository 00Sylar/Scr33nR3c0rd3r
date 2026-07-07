# UI Redesign — Feature Parity Contract

Every box below must be checked in the new web UI (`src/app_web.py` + `src/webui/`)
before `Scr33nX.bat` switches to it. Sources: README, `src/app.py` control walk
(2026-07-06, v1.6), `src/tray_win.py`. **Zero feature loss** is the acceptance bar.

## 1. App shell & header
- [ ] Window title `Scr33nX`, devil icon, red/black theme
- [ ] Logo + wordmark, `v{APP_VERSION}` next to it
- [ ] `● Update available (vX.Y)` clickable indicator (GitHub check on startup, silent offline)
- [ ] Bandwidth meters: `↓ X.X Mbps` (relay download) and `↑ X.X Mbps` (Telegram upload), live
- [ ] Global status pill: `● IDLE` / `● RECORDER ACTIVE` / `● SCANNER ACTIVE` / `● MONITORING (R+S)`
- [ ] `⛔ Terminate` button — hard-kill app + children; confirms only if a recording is active
- [ ] Minimize-to-tray behavior (when setting enabled)

## 2. Left sidebar
- [ ] Add Model: username-or-URL entry (site auto-detected from pasted URL), site dropdown
      (chaturbate / stripchat / camsoda / myfreecams), `＋ Add Model` button
- [ ] Live status panel: `● N LIVE`, total `N models`, per-site rows (CB/SC/CS/MFC — only
      sites present) with recording / online / total columns, `ALL` totals row

## 3. Recorder tab
### Table
- [ ] Columns: checkbox, MODEL, RANK (1–5 stars), STATUS, CURRENT FILE, SIZE, AUTO, SAVED
- [ ] Grouped by site with collapsible site header rows
- [ ] Status values with colors: RECORDING / ONLINE / OFFLINE / PRIVATE / CHECKING / errors
      (incl. `Low disk`); RECORDING shows current filename + growing size
- [ ] Star rank: click star sets rank, click same star clears; changing/clearing an
      *existing* rank asks confirmation (misclick guard); rating unranked is one-click
- [ ] Checkbox column: click toggles; header/selection interplay; `✓ N checked` / `N selected` label
- [ ] Shift+click range select, Ctrl+click toggle select (Treeview-equivalent)
- [ ] Column sorting with `↕`/arrow indicators: MODEL, RANK, STATUS, SIZE, AUTO, SAVED —
      stable while live updates stream in (v1.6 fix)
- [ ] Search box (filters by name) + `Status: All` filter dropdown — stable under live updates
- [ ] Live updates are diffed (no flicker, no scroll jumps)
### Toolbar
- [ ] `▶ REC`, `⏹ Stop`, `☑ Toggle AUTO`, `✕ Remove`, `✕ Remove Offline` (confirm first;
      only OFFLINE rows removed), `★ Add to Saved`
- [ ] `🧹 CLEAR RECORDER` (stop monitor + downloads, clear AUTO, remove all; Saved kept)
- [ ] `⏹ STOP ALL DOWNLOADS`, `▶ START MONITOR` / `⏹ STOP MONITOR` (+ `● STOPPING…` state)
### Context menu (single row)
- [ ] Start/Stop Recording, `☑/☐ Auto-Record`, `🎞 Max Quality (…)` per-model override submenu
- [ ] `⭐ Add to / ✕ Remove from Saved Models`
- [ ] `▶ Preview` (offline rows skipped with note)
- [ ] `🔗 Copy Model URL`, `🌐 Open in Browser`, `🌐 Open in Browser (choose…)`
- [ ] `📁 Open Output Folder`, `✕ Remove Model`
- [ ] `⭐ Set Rank` submenu (`☆ Clear rank`, 1–5)
- [ ] Check helpers: `☑ Check Selected (n)`, `☑ Check All Visible`, `☐ Uncheck All (n)`
### Context menu (multi-selection)
- [ ] Start/Stop Recording (n), Toggle AUTO (n), Max Quality (n), Open in Browser (n) /
      choose… (n), `📋 Copy as OneTab List (n)`, Remove (n), Set Rank for whole selection

## 4. Saved Models tab
- [ ] Header: `Saved Models · view-only status watchlist`, `N model(s)` count
- [ ] Table: checkbox, MODEL, RANK, STATUS, CURRENT FILE, SIZE (grouped by site, sortable,
      search + `Status: All` filter) — must stay smooth at ~4,000 rows (virtual scrolling)
- [ ] `▶ START SCANNER` / `⏹ STOP SCANNER`
- [ ] `＋ Add Current Recorder Model`, `📥 Import`, `📤 Export`
- [ ] Context menu: `＋ Add to Recorder`, Preview, Copy URL, Open in Browser (+choose…),
      Remove from Saved (single + n-selection), Copy as OneTab List (n), Set Rank, check helpers
- [ ] Ranks shared per-model with Recorder tab, persisted immediately

## 5. Output / Upload tab
- [ ] `▶ START PIPELINE` / `⏹ STOP PIPELINE` + status: `● STOPPED / STARTING / STAND BY /
      CONVERTING / UPLOADING / CONVERTING & UPLOADING / STOPPING`
- [ ] Stages checkboxes `① Convert .ts → .mp4`, `② Upload .mp4 to Telegram` — live re-tick
      while running (stand-by model; in-flight task never interrupted)
- [ ] Telegram / Pipeline settings: API ID, API Hash, Chat/Group ID, Topic ID, converted
      .mp4 folder, TDLib session folder + `💾 Save Pipeline Settings` (`Pipeline/pipeline_settings.json`)
- [ ] Missing-credentials handling: pipeline log message, Upload stays idle
- [ ] `🧙 Setup Wizard` — 4 steps (Welcome / API credentials / Destination / Review),
      Back/Next/Cancel/`✓ Save & Finish`, optional "start pipeline with Upload enabled now",
      phone + login-code prompts on first connect
- [ ] `🔑 Re-auth / Switch Account`
- [ ] Pipeline log pane: `Convert:` + `Upload 1..N:` live status lines

## 6. Activity Log tab
- [ ] Timestamped log view + `Clear`; same events also mirrored to rotating
      `%LOCALAPPDATA%\Scr33nX\streamrecorder.log`

## 7. Settings tab
- [ ] Output Folder (with picker), Max File Size (MB), Check Interval (sec)
- [ ] Max Quality (all models): Unlimited / 1080p / 720p / 480p
- [ ] Toggles: `⤵ Minimize to SysTray`, `🔔 Notifications`, `⚠ Dropped-Segment Warnings`,
      `⬇ Auto-Downgrade Quality`, `⛔ Stop All if Disk < 20 GB Free`,
      `🎭 Stripchat Browser Fallback`, `🔒 Privacy Mode`
- [ ] `Open links with`: Ask each time / System default / specific browser (+ first-use
      chooser dialog with "Remember my choice"; reset here)
- [ ] Stream preview: Mode (External window / Embedded), Preview engine (Auto/mpv/VLC),
      optional Player path
- [ ] `💾 Save Settings`; models/AUTO/Saved/ranks still persist immediately on every change
- [ ] `🔍 System Check` panel: detect ffmpeg, ffplay, mpv, VLC, python-vlc, python-mpv,
      tdjson, Playwright Chromium; `Add to PATH` + `Install` one-click fixes; `🔍 Re-check`

## 8. Stream preview
- [ ] Right-click → Preview on online/recording rows (both tabs); offline politely refused
- [ ] External mode: mpv → VLC → ffplay fallback, own process, plays via local relay
- [ ] Embedded mode: in-app player with play/pause/mute/volume
      (**new impl: hls.js in-page**; python-vlc/libmpv path retired at cutover)
- [ ] "Opening preview… Resolving the stream" transient notice; `✕ Close`

## 9. Dialogs & guards (all as in-page modals, never blocking the engine)
- [ ] Confirms: Terminate-while-recording, Remove Offline, rank change/clear,
      CLEAR RECORDER, second-instance warning ("port taken — close?")
- [ ] Browser chooser ("Open in which browser?" + remember)
- [ ] Privacy Mode cover: idle full-window cover (starfield), exit prompt `Exit / Stay`

## 10. Background behaviors (engine — regression-test only, code untouched)
- [ ] Monitor loop (poll interval), auto-record on live, auto-stop on offline
- [ ] File splitting `_partNNN` at Max File Size; filename format `name_SITE_date_time.ts`
- [ ] Quality pinning, per-model override > global cap, auto-downgrade ladder
- [ ] Low-disk guard: stop all + block starts under 20 GB, `Low disk` row error, auto-recover
- [ ] Stuck-RECORDING → offline stall probe (v1.6)
- [ ] Toasts: recording started/stopped/split/dropped-segments (respecting toggles)
- [ ] Tray: icon, `Show Scr33nX` / `Exit` / `Force Quit (Terminate)` menu
- [ ] Single-instance lock (port 5200) with warning + self-close offer
- [ ] Update check on startup

## 11. Local API / integrations (unchanged endpoints — verify while new UI runs)
- [ ] All 13 endpoints per README table (`/status`, `/dashboard`, `/add`, `/record`, `/auto`,
      `/rank`, `/remove`, `/stop_all`, `/clear`, `/monitor`, `/pipeline`, `/pipeline/stage`, `/quit`)
- [ ] Browser extensions (Chromium + Firefox) add/rank flows work; extension rank never prompts
- [ ] `scr33nx_ctl.py` subcommands; OpenClaw bot flows
- [ ] UI reflects API-driven changes live (add/remove/rank/record from extension or bot)

## 12. Launcher / packaging
- [ ] `Scr33nX.bat` launches new UI, no console window; `--classic` flag keeps old Tk UI
      for one release
- [ ] WebView2 presence check at startup with friendly install link if missing
- [ ] `requirements.txt`: + `pywebview`; python-vlc/libmpv marked droppable at cutover
