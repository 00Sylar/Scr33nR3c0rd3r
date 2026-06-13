# Changelog

All notable changes to **Scr33nX** are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Dates are `YYYY-MM-DD`. Versioning starts at **V1.0**; earlier entries are
grouped by date / milestone.

---

## [Unreleased]

### Added
- **Chat-bot / agent control via the local API (OpenClaw).** Scr33nX can now be
  driven from a messaging app (Telegram/WhatsApp) through an OpenClaw agent that
  calls the local API. New control script `scr33nx_ctl.py` wraps every action
  (record / stop / stop-all / add to recorder or saved / remove / AUTO on-off /
  status / start-stop the recorder monitor, saved scanner, and Telegram pipeline /
  open / close the app). Full walkthrough in the new **`OPENCLAW-HOWTO.md`**.
- **New local-API endpoints (port 5200).** Added `POST /stop_all` (stop every
  download + clear AUTO), `POST /monitor {target, enabled}` (start/stop the
  recorder monitor or saved scanner), `POST /pipeline {enabled}` (start/stop the
  Telegram pipeline), and `POST /quit` (graceful shutdown). `POST /remove` now
  also accepts `target: "saved"` to remove from Saved Models. All API-triggered
  actions use no-dialog code paths so they never block the UI thread that serves
  the API.
- **OneTab / browser integration.** Right-click on either tab now offers
  "🌐 Open in Browser" (opens each model's page as a browser tab; asks for
  confirmation above 10 tabs) and, for multi-selections, "📋 Copy as OneTab
  List" — copies one `URL | name (site)` line per model, ready to paste into
  OneTab's Import/Export URLs page. Both act on the checked set when boxes
  are checked, otherwise on the highlighted selection.
- **Status filter on both tabs.** A "Status ▾" dropdown next to the name
  filter with one checkbox per status (Online, Recording, Offline, Private,
  Checking, Error) — any combination works (e.g. Online + Recording).
  Nothing checked = show all. The view updates live: a model whose status
  changes moves in/out of the filtered list automatically, and the name
  filter and status filter combine.
- **Row checkboxes on both tabs.** A ☐/☑ box at the start of each model row
  builds an explicit working set: when any rows are checked, every bulk
  action (REC, Stop, Toggle AUTO, Remove, Add to Saved, right-click menu)
  operates on the checked rows instead of the click-selection. The counter
  label shows "✓ N checked" (click it to clear). Right-click offers
  "Check All Visible" (respects active filters — e.g. filter to Online,
  check all, act) and "Uncheck All". Checks survive filtering and sorting.
- **Faster multi-selection.** Press-and-drag across rows selects the whole
  range (with edge auto-scroll); Shift+click range-select and Ctrl+click
  toggle now work everywhere in the row (the checkbox zone ignores modified
  clicks); right-click "Check Selected" converts the highlight into checked
  boxes in one step.
- **Saved-tab bulk actions.** Right-click acts on the checked set (or the
  multi-selection): "Add to Recorder (N)" with duplicate-skipping and
  "Remove from Saved (N)" with a single log/persist instead of N.

---

## V1.0 — 2026-06-11

### Added
- **Filter boxes on the Recorder and Saved Models tabs.** Type to show only
  matching model names (debounced; hidden rows keep updating and reappear
  when the filter clears). The Saved tab shows a "X / Y shown" counter.
- **Lazy Saved Models tab.** With a large watchlist (1500+ models) the tab
  used to cost startup time, Tk memory, and engine overhead even when never
  opened. Rows are now built on the first visit to the tab, and models are
  registered in the recording engine only when the scanner starts. Status
  mirroring is preserved: a model recording in the Recorder tab shows
  RECORDING in Saved Models the moment the rows are built, and live updates
  take over from there. Export/import/persistence now read from the data
  list, so nothing is lost even if the tab is never opened.

### Fixed
- **Tray-icon hard crashes (access violations).** Two combined causes: several
  Win32 calls lacked 64-bit `restype` declarations, so ctypes truncated
  pointer-sized handles (`GetModuleHandleW`/`LoadIconW`/`CreatePopupMenu`),
  and the tray window class was re-registered on every minimize with a fresh
  WNDPROC trampoline while the previous one could still be referenced by a
  live window. The class is now registered once per process with a single
  persistent trampoline, and tray creation no longer blocks the UI thread
  (the old code could freeze the window for up to 5 s).
- **Recordings stalled after the first file split.** Split parts relaunched
  ffmpeg without a stderr-drain thread; once the pipe buffer filled, ffmpeg
  blocked mid-write until the stall detector killed it. Every launch path now
  drains stderr.
- **Auto-restart could resurrect a stopped recording.** The guard intended to
  block restarts after a stop was dead code (`if not self._running:` on a
  dict that is always truthy). Explicit stops now set a per-model flag the
  delayed restart respects. Restarts also re-resolve the stream URL instead
  of falling back to the expired one (which burned a restart attempt on a
  guaranteed failure).
- **Quit no longer freezes the window.** Closing with many active recordings
  ran the full ffmpeg flush (up to ~20 s) on the UI thread; it now runs in
  the background while the header shows STOPPING…, and quitting from the
  tray first restores the window so the confirm dialog is visible.

### Changed — UI smoothness with many recordings
- **Privacy-mode starfield (and the whole UI) no longer stutters under load.**
  Worker-thread log lines (ffmpeg stderr, relay warnings) were posted as one
  Tk event per line and flooded the event loop; they are now queued and
  inserted in one batch per 250 ms tick, and the log only autoscrolls when
  actually visible. Per-model file-size timers (blocking `getsize` calls on
  the UI thread) were replaced by a single worker-thread sweep that posts one
  batched update. Header stats are recomputed at most twice per second
  instead of on every status callback.
- **Starfield is time-based and cheaper.** Motion now advances by elapsed
  time (late frames no longer freeze-and-jump the stars), star widths are
  only rewritten when they visibly change, and the animation pauses while
  the window is minimized.
- **Monitor checks run in parallel.** Due online checks go through a small
  thread pool (8 workers) instead of one serial pass, so one slow site no
  longer delays every other model's check and the split/stall housekeeping.
  Chaturbate API calls remain globally rate-limited as before.
- **Relay housekeeping.** The prefetch cache size is tracked incrementally
  (the old per-refresh full-cache scan ran under the global lock), and a
  janitor thread prunes expired segments — previously, stopping all
  recordings could leave up to 768 MB of cached segments in RAM forever.
- **Logs moved out of the app folder** to `%LOCALAPPDATA%\Scr33nX`
  (`streamrecorder.log`, `streamrecorder_crash.log`). The app folder is often
  cloud-synced (OneDrive), where sync locks break log rotation. The crash log
  also restarts once it exceeds 1 MB instead of growing forever. The app now
  warns when the recordings output folder itself is inside a cloud-synced
  directory.

---

## 2026-06-11 — High-concurrency fixes & quality control

### Added
- **Quality caps.** Global "Max Quality (all models)" dropdown in Settings
  (Unlimited / 1080p / 720p / 480p) plus a per-model override in the model
  right-click menu (single and multi-select). Resolution order:
  per-model → global → unlimited. Applied by the relay when a recording
  (re)starts; if a stream has no variant at/below the cap, the lowest
  available is used. Stripchat is not capped (it bypasses variant selection).
- **⬇ Auto-Downgrade Struggling Streams** (Settings checkbox, off by default).
  A stream that loses ≥10 s of video within a 60 s window is restarted one
  quality step lower (720p → 480p → 240p), with a 2-minute cooldown between
  steps. Only the struggling stream is touched; models with a manual quality
  override are never auto-downgraded. The downgrade is session-only — it
  resets when the model's recording ends.
- **Beta log file in the app directory.** `streamrecorder.log` (rotating,
  5 MB × 3) next to `app.py` captures the Activity Log, ffmpeg stderr, relay
  warnings, and thread tracebacks; crash dumps go to
  `streamrecorder_crash.log`. Previously a 1 MB hidden file in the home dir.

### Fixed
- **Mass segment loss with many concurrent recordings.** The relay's shared
  prefetch pool starved at ~10–15 simultaneous streams: 16 workers, each
  stallable for up to 60 s (3 × 20 s retries), and a 300 MB cache cap that
  silently disabled prefetching entirely. Now 64 workers, fail-fast segment
  fetches (2 tries, 5 s connect / 10 s read), 768 MB cache cap with a logged
  warning when hit, and a larger upstream connection pool (32/128).
- **ffmpeg "Error number -138" connecting to the relay.** The relay's listen
  backlog was the Python default of 5 pending connections; raised to 128 so
  dozens of concurrent ffmpeg processes don't get connection-refused.
- **Bogus upload-meter spikes** (e.g. ↑760 Mbps on a 200 Mbps line): TDLib
  reports a file as instantly uploaded when Telegram dedupes or resumes it;
  such physically implausible samples are now discarded.

## 2026-06 — Documentation

### Added
- **`RercordingLogics.md`** — full per-site technical reference ("how to make it
  work") covering the relay, resolvers, Stripchat MOUFLON/Playwright paths, the
  MyFreeCams FCS websocket, ffmpeg invocation, exit codes, and a troubleshooting
  map. Written so another engineer or LLM can pick up the recording pipeline cold.
- **This `CHANGELOG.md`** to track changes going forward.

---

## 2026-06 — MyFreeCams support

### Added
- **MyFreeCams (`MFC`) recording.** New `mfc.py` resolver speaks MFC's FCS chat
  protocol over a guest websocket (no public API exists): fetches `serverconfig.js`,
  connects `wss://{xchat}.myfreecams.com/fcsl`, does a USERNAMELOOKUP, maps the
  video-state (`vs`) to online/away/private/offline, and probes candidate HLS edge
  URLs (`f4v_cmaf` / `f4v_mobile`) until one answers. Records through the existing
  relay + `ffmpeg -c copy` path.
- MFC integrated into the saved-watchlist bulk scanner (one websocket per sweep),
  with `PRIVATE` status + 5-minute cooldown for away/private/group shows.
- `websocket-client` added to `requirements.txt`.

## 2026-06 — Branding & icons

### Changed
- App now sets its Windows `AppUserModelID` so the taskbar shows the devil icon
  instead of python.exe's default icon.
- Browser extensions (Chromium **and** Firefox) use the red devil icon in the
  manifest and popup header.
- Devil app icon recolored to brand red for contrast on the dark theme.

### Added
- Devil app icon, upload-speed meter, and settings-checkbox icons.

## Earlier — Reliability, relay & UI

### Added
- **Segment-drop fix for concurrent recordings:** the local relay (`cb_relay.py`)
  now prefetches upcoming HLS segments in parallel (16 workers, in-memory cache)
  so slow streams catch up instead of dropping 1–2 s chunks.
- **Live bandwidth meter** (`↓ X.X Mbps`) in the header — counts every byte the
  relay fetches upstream.
- **Dropped-segment warnings** — toast + Activity Log entry when a stream loses
  segments because bandwidth can't keep up (toggle in Settings).
- **Highest-bitrate variant pinning** through the relay, so ffmpeg can never
  silently fall back to a lower resolution mid-recording.
- **Native (browserless) Stripchat recording** via MOUFLON decryption
  (`stripchat_native.py`), with automatic Playwright/Chromium fallback
  (`stripchat_live.py`) when keys rotate or the model isn't public.
- Camsoda recording (highest-quality, via relay).
- "Copy Model URL" right-click menu entry.
- Site hashtag in Telegram upload captions.

### Changed
- App renamed to **Scr33nX**; UI redesigned to an elegant black & red minimalist
  theme.
- Improved recorder reliability and overall app UI/settings.
- Hide the console window of ffmpeg spawned inside `stripchat_live`.

### Fixed
- Corrupted `.ts` files: ffmpeg is now shut down gracefully (`q`) so the MPEG-TS
  trailer flushes.
- Camsoda recording (extension whitelist / relay routing).
