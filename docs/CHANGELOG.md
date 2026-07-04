# Changelog

All notable changes to **Scr33nX** are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Dates are `YYYY-MM-DD`. Versioning starts at **V1.0**; earlier entries are
grouped by date / milestone.

---

## [Unreleased]

### Added
- **⛔ Low-disk guard** (Settings checkbox, off by default). When enabled and
  the drive holding the output folder falls below **20 GB free**, all active
  recordings are stopped immediately and every new start — manual REC,
  auto-rec, auto-restart, and max-size file splits — is blocked until you
  free up space or disable the option. You get one toast when the guard
  trips and a log line when space recovers.
- **Rank misclick guard (app only).** Clicking the RANK stars on a model that
  already has a rank now asks for confirmation ("★★★★☆ → ★★☆☆☆?") before
  changing or clearing it. Rating an unranked model stays one-click, and the
  browser extension / bot API are never prompted.
- **Single-instance lock.** Launching Scr33nX while another instance is
  already running (control port 5200 taken) now shows "You can only open one
  instance of this app" and the new window closes itself. Two live instances
  share one settings file and silently overwrite each other's models and star
  ranks — the most likely cause of "my ranks disappeared after a restart".
- **✕ Remove Offline button** (Recorder toolbar, next to ✕ Remove) — removes
  every model whose status is currently **OFFLINE** in one click, after a
  confirmation. Goes by the visible status, so RECORDING / PRIVATE / CHECKING
  / ERROR rows are kept, and Saved Models are never touched.

### Changed
- **Launcher renamed** `StreamRecorder.bat` → **`Scr33nX.bat`** to match the
  app name. Update any shortcuts pointing at the old name; the bot's `open`
  command uses the new name automatically.

### Fixed
- **Faster RECORDING → offline detection.** When a model went offline,
  ffmpeg often kept reconnecting instead of exiting, so the status stayed
  **RECORDING** for up to ~75 s until the 60 s stall timeout fired. Now, when a
  recording's file stalls for ~20 s, the app quietly asks the resolver whether
  she's still online (off the monitor thread, so no slowdown) — if she's
  offline it stops the recording right away (≈25 s instead of ~75 s); if she's
  just buffering it's left alone. The 60 s stall hard-stop remains as a backstop.
- **Stripchat recordings no longer get stuck on RECORDING after a model goes
  offline.** When a Stripchat model goes offline, the CDN often swaps her live
  playlist for a looping advert placeholder — the recorder kept downloading
  those filler segments, the file kept growing, and stall detection never
  triggered, so the row showed **RECORDING** indefinitely (recording adverts).
  The relay now spots the advert markers on every playlist refresh (no extra
  network traffic or CPU) and stops the recording immediately → **OFFLINE**.
- **Stall detection now also covers recordings that never create their output
  file.** A recorder process that hung before writing anything was invisible
  to the stall check and stayed **RECORDING** forever; a missing file now
  counts as 0 bytes, so the normal 20 s probe / 60 s stop applies.
- **Column sorting no longer resets while a status filter is active.** With
  e.g. *Online* filtered and the list sorted by rank, every status update
  re-applied the filter and shuffled rows back to their original order, so
  the sort had to be redone over and over. The last-clicked sort is now
  remembered and re-applied after every filter pass (Recorder and Saved tabs).

---

## V1.5 — 2026-06-28

In-app stream preview, a dedicated Settings tab with a dependency **System
Check**, a guided Telegram setup, a split stand-by pipeline, a browser picker,
and a round of UI-freeze fixes.

### Added
- **▶ Stream preview (mpv / VLC / ffplay).** Right-click a model → **Preview**
  to watch its live stream (available on both the Recorder and Saved Models
  tabs). A brief "Opening preview…" indicator shows while the stream resolves,
  then the player appears and is brought to the foreground. Choose **Mode**
  (External window / Embedded in-app) and **Preview engine** (Auto / mpv / VLC)
  in Settings:
  - *External* launches a standalone **mpv**, **VLC**, or **ffplay** window in its
    own process (minimal impact on recording) — ffplay (bundled with ffmpeg)
    works with no extra install.
  - *Embedded* plays inside a window with play/pause/mute/volume via **python-vlc**
    (easiest — auto-finds an installed VLC, no DLL step) or **python-mpv**
    (needs libmpv-2.dll in the `src/` folder). If neither is available you're told
    and offered the external player instead.
  Playback goes through the same local relay the recorder uses; an optional
  player-path setting overrides auto-detection. The `python-vlc` bridge ships in
  `requirements.txt`, and if it's ever missing while VLC is installed, the app
  offers a **one-click install** the first time you open an embedded preview.
- **Choose which browser "Open in Browser" uses.** Right-click a model →
  **Open in Browser** now lets you pick the browser (System default, or any of
  Chrome / Edge / Firefox / Brave / Opera / Vivaldi detected on your PC) the
  first time, with a *Remember my choice* option so you're not asked again. A new
  **Open links with** dropdown in Settings changes or resets the saved default at
  any time, and an **Open in Browser (choose…)** menu entry re-opens the picker
  for a one-off browser without touching your saved default.
- **Telegram Setup Wizard.** A new **🧙 Setup Wizard** button on the Output /
  Upload tab walks first-time users through configuring the upload pipeline:
  API ID / Hash (with a link to my.telegram.org), the destination group/topic
  ID, and optional folders. It saves everything to the normal settings and can
  start the pipeline straight away — the phone-number/login-code prompts then run
  through the usual login flow.
- **Split the pipeline into independent Convert and Upload stages, with a
  live stand-by model.** The Output / Upload tab now has two **Stages**
  checkboxes — *① Convert .ts → .mp4* and *② Upload .mp4 to Telegram*. The
  pipeline starts even with nothing checked and sits in **● STAND BY**; tick
  stages at any time and they apply immediately (the header shows *CONVERTING*,
  *UPLOADING*, or *CONVERTING & UPLOADING*). Unchecking a stage stops it after
  its current task finishes — an in-progress conversion or upload is never
  interrupted. Run Convert alone to get `.mp4` files without uploading, Upload
  alone to send `.mp4`s you already have, or both for the full flow. Enabling
  Upload connects to Telegram on demand (reusing your saved session — no restart,
  no re-login); credentials are only needed when Upload is on. Stage choices
  persist across restarts.
- **Control the pipeline stages from the OpenClaw bot.** New
  `scr33nx_ctl.py pipeline convert on|off` and `pipeline upload on|off` commands
  tick/untick the stages from your phone — working whether the pipeline is
  running or stopped. Backed by a new `POST /pipeline/stage` local-API endpoint.
- **System Check panel (⚙ Settings).** A dependency validator showing whether
  each external tool / package is found: ffmpeg, ffplay, mpv, VLC, python-vlc,
  python-mpv + libmpv, tdjson, and Playwright Chromium. It catches the common
  "installed but not on PATH" case (e.g. mpv) and offers one-click fixes — **Add
  to PATH** (appends to your user PATH, no admin, applied immediately),
  **Install** for the Python packages / the Stripchat browser, and **Re-check**.
  System apps (mpv/VLC) are detected and guided rather than silently installed.

### Changed
- **Settings moved to their own ⚙ Settings tab.** All settings now live in a
  dedicated, scrollable **⚙ Settings** tab; the left panel keeps just **Add
  Model** and the live status panel, so adding models stays one click away.

### Fixed
- **Preview an offline model no longer crashes the app.** An offline model
  resolved to a dead stream that could hard-crash the in-process player
  (libVLC/libmpv). Preview now runs only for **online or recording** models and
  shows a clear note otherwise.
- **Stripchat preview now works.** Preview resolves Stripchat through the same
  browserless MOUFLON path the recorder uses, so the relay can decrypt it.
- **"Settings saved" confirmation.** Saving settings shows a brief on-screen
  confirmation that auto-clears, and warns when the chosen Preview engine isn't
  installed (so the fallback is no longer a silent mystery).
- **UI no longer freezes when opening many model pages at once.** "Open in
  Browser" launches the tabs on a background thread instead of blocking the app.
- **Smoother UI under heavy load (monitor + scanner, recording storms).** Engine
  status updates are applied in small coalesced batches (~150 ms) instead of one
  event per model, so a scan pass or a burst of recordings no longer stalls the
  event loop.
- **Removed a UI-thread lock contention.** The status handler no longer grabs the
  recorder lock on the UI thread during a recording storm.

---

## V1.4 — 2026-06-26

First release published as an official GitHub Release — from this version on,
Scr33nX checks GitHub on startup and flags when a newer build is available.

### Added
- **Version number in the header.** The running build (e.g. `v1.3`) now shows
  next to the Scr33nX logo, so it's easy to tell which version you have when
  comparing with someone else. Driven by a single `APP_VERSION` constant.
- **Automatic update check.** On startup Scr33nX checks GitHub for the latest
  published release in the background. If a newer version exists, a clickable
  `● Update available (vX.Y)` indicator appears in the header and opens the
  releases page. It fails silently when offline and never interrupts you with a
  popup. *Note: requires releases to be published on GitHub, not just tags.*
- **Rank models from the OpenClaw bot.** New `scr33nx_ctl.py rank <model> 0-5`
  command sets a model's star rank, and `add-saved` / `add-recorder` / `record`
  gained a `--rank N` option so *"save her and rank 5"* adds **and** rates in one
  step. A bare `rank` requires the model to already be in Saved Models or the
  Recorder (same rule as the app/extension); the bot relays the error otherwise.
  Wired into the OpenClaw command map in `OPENCLAW-HOWTO.md`.

- **⛔ Force Quit / Terminate.** A new red **Terminate** button in the header
  (and a **Force Quit (Terminate)** item in the system-tray right-click menu)
  instantly hard-kills Scr33nX and its whole child-process tree — ffmpeg, the
  relay, any Playwright/Chromium — like Task Manager's *End Task*, instead of the
  graceful Quit that flushes recordings first. It confirms only when a recording
  is active, so an idle app dies immediately and a misclick mid-recording can't
  silently drop footage.

### Changed
- **Reorganized the project into a cleaner layout.** All Python source (and
  `icons/`) now lives under `src/`, and the docs (`CHANGELOG`, `CONTRIBUTING`,
  `OPENCLAW-HOWTO`, the renamed `RecordingLogics`) under `docs/`. The repo root
  is now just `README`, `CLAUDE.md`, `requirements.txt`, `StreamRecorder.bat`,
  and the top-level folders. **No change to how you run it** — double-click
  `StreamRecorder.bat` exactly as before. Stray/private files (a leftover
  `saved_models.json`, the outdated `Sample Content/` samples) were removed from
  the repo and are now git-ignored.

### Fixed
- **Taskbar icon could show blank.** With the app's explicit taskbar identity
  set, Tk's `iconbitmap`/`iconphoto` didn't reliably reach the Windows taskbar
  button, leaving a blank icon. The `.ico` is now pushed to the taskbar directly
  via `WM_SETICON` (both icon sizes), with 64-bit handle types declared so the
  handles aren't truncated.
- **Ranks no longer linger in memory after a model leaves both lists.** Removing
  a model from Saved Models (or the Recorder) while it's on no other list now
  drops its rank from the live session too, matching the on-save pruning — so a
  removed model can't report a stale rank until the next restart.

---

## V1.3 — 2026-06-24

### Added
- **⭐ 1–5 star model ranks.** Rate models on both the Recorder and Saved
  Models tabs: a sortable **RANK** column where you click a star to set 1–5
  (click the current star again to clear), or right-click → **Set Rank** to
  rate a single row or a whole checked/selected set at once. Ranks are keyed
  by model identity, so the same model shows the same stars on both tabs and
  in the browser extension, and they persist between sessions (in
  `~/.streamrecorder_config.json` under `ranks`). The Saved Models **export
  now carries ranks and merges** into an existing export file (keeps
  file-only entries, refreshes ranks) instead of overwriting; **import
  back-fills** stars onto models you already have.
- **Rank from the browser extension.** The popup (Chromium **and** Firefox)
  shows a clickable star row for the model on the current page. To avoid
  "orphan" ranks with no row to manage them, rating is only enabled once the
  model is in **Saved Models or the Recorder** — otherwise the stars are
  shown disabled with a hint. New `POST /rank {name, site, rank}` endpoint
  (rejects ranking a model that isn't on a list); `GET /status` now also
  returns `rank`.
- **Live polling in the browser-extension popup.** While the popup is open it
  keeps polling the backend, so a model's status (and rank / list membership)
  updates in place without closing and reopening it.
- **Status dashboard (left panel).** The single "N recording · N offline" line
  is now a per-site + totals breakdown: one row per site that has models —
  🟡 CB, 🔴 SC, 🔵 CS, 🟢 MFC (colors match each site's brand) — showing
  total / ▶ recording / ● online / ○ offline, plus an ALL summary line.

### Fixed
- **Privacy Mode could freeze the whole app.** Moving or resizing the window
  while the idle starfield cover was up popped a **modal** "Exit privacy mode?"
  dialog that rendered *behind* the full-window cover — the window kept the
  modal's input grab but the dialog was invisible, so the UI looked frozen
  (recordings and background threads kept running). Moving the window no
  longer prompts; clicking the cover now shows an **Exit / Stay panel drawn on
  the cover itself** — no modal, no grab, nothing that can hang the event loop.
- **UI freeze when starting (or stopping) many recordings at once.** A burst
  of starts — the monitor finding many models online, AUTO firing on all of
  them, or "Start" on a large selection — spawned one thread *and one ffmpeg
  subprocess per model simultaneously*, storming the CPU/disk and freezing
  the window. Launches now go through a bounded pool (4 concurrent) with
  per-model dedupe, so a big batch staggers smoothly instead of stampeding.
- **Silent duplicate-instance.** Starting a second Scr33nX while one was
  already running left the new one unable to bind the API port (5200); it ran
  half-working, and its tray icon and the browser extension actually
  controlled the *other* instance. The failed bind is now logged and shown in
  the Activity Log with a clear "another Scr33nX is likely running" warning.
- **Saved Models desync between the API and the lazy-built UI rows.** Adding or
  removing models via the extension/bot now stays consistent with the
  on-screen Saved rows even when the Saved tab hasn't been opened yet.

---

## V1.2 — 2026-06-13

### Added
- **🧹 CLEAR RECORDER button** (next to STOP ALL DOWNLOADS). One click to a
  clean slate: stops the recorder monitor, force-stops every active download,
  unchecks all AUTO, and removes every model from the Recorder (all sites).
  Saved Models are untouched. Confirms first. Also exposed to the bot via the
  new `POST /clear` endpoint and `scr33nx_ctl.py clear` command.
- **Bot dashboard + clear (OpenClaw).** New `GET /dashboard` endpoint returns the
  aggregate per-site (CB/SC/CS/MFC) + totals snapshot, and `POST /clear` clears
  the Recorder. Wired into `scr33nx_ctl.py` as `dashboard` and `clear` commands,
  so you can ask the bot "Scr33nX dashboard status" or "clear the recorder".
- **🎭 Stripchat Browser Fallback toggle (Settings).** Gates the Playwright
  browser fallback used when Stripchat's browserless native path can't resolve
  a stream. Enabled (default) = unchanged behavior. Disabled = native path
  only; if it fails the stream is simply not recorded and Playwright never
  launches under any circumstance.
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

### Changed
- **Standardized output filenames across all sites.** A recording that never
  splits now keeps NO part suffix (`modelname_CB_20240515_143022.ts`); the
  moment a size-split happens, the first segment is renamed and the set reads
  `_part001`, `_part002`, … All parts of one recording share a single
  timestamp, and the Telegram-pipeline `.mp4` splits use the same 3-digit
  `_partNNN` padding as the recorder (was `_part1`).

### Fixed
- **STOP TELEGRAM PIPELINE now halts the uploader workers.** Previously the
  converter stopped but the Telegram upload clients kept draining the queued
  backlog. Stopping now lets the in-flight upload finish, then halts the
  uploaders and closes the TDLib clients until the pipeline is resumed.

---

## V1.1 — 2026-06-12

### Added
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
