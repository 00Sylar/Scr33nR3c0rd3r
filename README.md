# Scr33nX

A lightweight Windows desktop app for automatically recording live streams from **Chaturbate**, **Stripchat**, **Camsoda**, and **MyFreeCams** — at the **highest available quality** by default, with optional global and per-model quality caps for heavy multi-model sessions.

Recordings are pulled through a local smart relay that prefetches HLS segments in parallel, so dozens of simultaneous recordings stay smooth instead of dropping segments when bandwidth gets tight.

---

## Features

### Recording
- ✅ Auto-detects when a model goes online and starts recording immediately
- ✅ Record multiple models simultaneously across Chaturbate, Stripchat, Camsoda, and MyFreeCams
- ✅ **Highest-quality pinning** — the relay locks ffmpeg to the top-bitrate variant so it can never silently fall back to a lower resolution
- ✅ **Quality caps** — a global "Max Quality" setting (Unlimited / 1080p / 720p / 480p) plus per-model overrides (right-click a model) for fitting many simultaneous recordings into your bandwidth; per-model beats global
- ✅ **Auto-downgrade (optional)** — when enabled, a stream that persistently loses segments is restarted one quality step lower (720p → 480p → 240p) without touching the other recordings; resets when that model's session ends. Models with a manual quality override are never auto-downgraded
- ✅ **Parallel segment prefetching** — segments are downloaded ahead of ffmpeg in parallel (64 shared workers); slow streams catch up instead of skipping 1–2 s chunks — built for dozens of concurrent recordings
- ✅ **File splitting** — automatically starts a new file when the recording reaches your defined max size (e.g. 3070 MB)
- ✅ Stripchat records browserless (native MOUFLON path) when possible, with automatic Playwright/Chromium fallback

### Monitoring & UI
- ✅ Modern black & red UI — a native window (Windows WebView2, no browser involved) with smooth animations, shadows, drag-rectangle multi-select, and collapsible per-site groups. The previous Tk interface is still available (see *Two interfaces* below)
- ✅ **Version number** shown in the header next to the logo (`v2.0`) so you always know which build you're running
- ✅ **Update check** — on startup Scr33nX quietly asks GitHub for the latest release; if a newer one exists, a clickable `● Update available` indicator appears in the header and opens the releases page. Runs in the background and fails silently when offline
- ✅ **Live bandwidth meter** in the header (`↓ X.X Mbps` download / `↑` Telegram upload) showing Scr33nX's total traffic — your indicator for when you're approaching your internet connection's limit
- ✅ **Beta log file** — everything (Activity Log, ffmpeg stderr, relay warnings, crash tracebacks) is also written to `%LOCALAPPDATA%\Scr33nX\streamrecorder.log` (rotating, 5 MB × 3)
- ✅ **Dropped-segment warnings** — get notified when a stream is losing segments because bandwidth can't keep up (toggle in Settings)
- ✅ **▶ Stream preview** — right-click a model → **Preview** to watch its live stream. Opens in a standalone **mpv / VLC / ffplay** window by default (own process, minimal impact on recording), or an optional **embedded in-app** player (mpv or VLC) with play/pause/mute/volume. Pick the engine in Settings; plays through the same local relay the recorder uses
- ✅ **✕ Remove Offline** — one-click toolbar button that removes every model currently showing **OFFLINE** from the Recorder (asks first; recording/private/checking rows and Saved Models are untouched)
- ✅ **Saved Models** tab — view-only watchlist with online/offline status
- ✅ **⭐ 1–5 star ranks** — rate any model on the Recorder or Saved Models tab (click a star, click it again to clear; or right-click → **Set Rank** for one row or a whole selection at once). Sortable **RANK** column, shared per-model across both tabs, and saved between sessions
- ✅ Windows desktop notifications (recording started/stopped/split/dropped segments)
- ✅ Minimize to system tray
- ✅ **⛔ Force Quit / Terminate** — a header button (and tray-menu item) that hard-kills Scr33nX and all its child processes (ffmpeg, relay, Chromium) instantly, like Task Manager's *End Task*; confirms first only if a recording is active
- ✅ 🔒 Privacy Mode — idle screen cover
- ✅ Activity log with timestamps
- ✅ Settings saved between sessions (models, output folder, max size, etc.)

### Integrations
- ✅ Browser extension (Chromium **and** Firefox) — one-click add from a model's page, plus 1–5 star rating right from the popup (for models already in Saved Models or Recorder); local API, port 5200
- ✅ Telegram upload pipeline (optional) — converts finished recordings and uploads them to a Telegram group/topic
- ✅ **Chat-bot / agent control (OpenClaw)** — drive Scr33nX from your phone over Telegram/WhatsApp: text a link to record, stop one or all, add/remove from Saved, toggle the monitors/scanner/pipeline, and even open or close the app. Everything runs through the same local API. Full setup: **[OPENCLAW-HOWTO.md](docs/OPENCLAW-HOWTO.md)**.

---

> 📜 See **[CHANGELOG.md](docs/CHANGELOG.md)** for a running history of changes, **[RecordingLogics.md](docs/RecordingLogics.md)** for the deep technical recording reference, and **[CONTRIBUTING.md](docs/CONTRIBUTING.md)** for the documentation/release workflow.

---

## Requirements

- **Windows 10/11**
- **WebView2 Runtime** — required by the default UI, built into Windows 11; on Windows 10 it's normally installed already via Edge auto-update. If missing, the app tells you and links the installer (or use `Scr33nX-Classic.bat`, which doesn't need it — see *Two interfaces* below)
- **Python 3.10+** — https://python.org (check "Add Python to PATH" during install)
- **ffmpeg** — https://ffmpeg.org/download.html (its **ffplay** is used for the default external preview if mpv isn't installed)
- **Playwright Chromium** — only needed as the Stripchat fallback recorder
- **Preview player** — choose the engine in **⚙ Settings → Preview engine** (Auto / mpv / VLC):
  - *External preview* works out of the box with **ffplay** (bundled with ffmpeg). Install **mpv** (https://mpv.io) or **VLC** (https://videolan.org) for nicer windows.
  - *Embedded (in-app) preview*: the default UI has a **built-in player** — no extra install needed. The classic UI (`--classic`) uses **VLC** by default — install **VLC** (https://videolan.org); the `python-vlc` bridge is in `requirements.txt` and auto-finds your VLC (no DLL/PATH step; if the bridge is somehow missing, the classic UI offers a one-click install).
  - *mpv as the classic UI's embedded engine (alternative):* `pip install python-mpv` **and** drop the 64-bit **`libmpv-2.dll`** (from an *mpv-dev* build, e.g. https://sourceforge.net/projects/mpv-player-windows/files/libmpv/) into the Scr33nX **`src/`** folder — the app adds that folder to its DLL search path automatically.

---

## Installation

### 1. Install Python
Download from https://python.org. During install, **check "Add Python to PATH"**.

### 2. Install ffmpeg
Pick one:
- `winget install Gyan.FFmpeg` (easiest — the app finds the WinGet install automatically), **or**
- Download `ffmpeg-release-essentials.zip` from https://www.gyan.dev/ffmpeg/builds/, extract it, and place `ffmpeg.exe` in the Scr33nX folder, **or**
- Add ffmpeg to your system PATH.

### 3. Install Python dependencies
From the Scr33nX folder:
```
pip install -r requirements.txt
playwright install chromium
```
(`playwright install chromium` is only required for the Stripchat browser fallback — skip it if you don't record Stripchat.)

### 4. Run the app
Double-click **`Scr33nX.bat`** (launches the GUI with no console window).

### Two interfaces

Scr33nX ships with two interfaces on the same recording engine, same
settings, and same config files — pick whichever you prefer:

| | Launcher | Notes |
|---|---|---|
| **Default (recommended)** | `Scr33nX.bat` (or `Scr33nX-WebUI.bat`) | Modern native window (Windows WebView2) — smooth animations, drag-select, built-in preview player |
| **Classic** | `Scr33nX-Classic.bat` (or `Scr33nX.bat --classic`) | The original Tk interface, unchanged |

Only run **one instance at a time** — both share the same control port
(5200), and starting a second one closes itself with a warning.

### 5. Install the browser extension (optional)

The extension adds models to Scr33nX with one click while you're on their page, and lets you set a 1–5 star rank from the popup once the model is in Saved Models or the Recorder. The app must be running (it listens on `http://localhost:5200`).

**Chromium browsers (Chrome / Brave / Opera / Edge):**
1. Open `chrome://extensions` (or `brave://extensions`, `opera://extensions`, `edge://extensions`)
2. Enable **Developer mode** (toggle in the corner)
3. Click **Load unpacked** and select the `extension/Chromium` folder

**Firefox:**
1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on…**
3. Select `extension/Firefox/manifest.json`

> Firefox removes temporary add-ons on restart; reload it after restarting, or package it as a signed add-on for a permanent install.

---

## Usage

*The steps below apply to both interfaces — the new default UI and the classic one (`--classic`) behave the same way; only the look differs.*

1. **Add models** using the left panel — enter the username (or paste a full model URL; the site is auto-detected) and select the site: `chaturbate`, `stripchat`, `camsoda`, or `myfreecams`
2. Click **▶ START MONITOR** — the app polls each model at the configured interval
3. When a model goes live, recording starts automatically and you'll get a notification
4. When the model goes offline, the recording stops automatically
5. If you set a **Max File Size**, the recording splits into numbered parts (`_part001`, `_part002`, …) automatically; a recording that never reaches the limit keeps a single, unsuffixed file
6. Watch the **↓ Mbps** meter in the header while recording several models — if your streams start dropping segments you'll also get a warning notification
7. **Rate models** — give any model 1–5 stars on the Recorder or Saved Models tab (click a star in the **RANK** column, or right-click → **Set Rank** to rate a whole selection), then click the `RANK` header to sort by it. You can also rate from the browser-extension popup. Ranks stay with the model across both tabs and persist between sessions
8. **Preview a stream** — right-click an **online (or recording)** model on the **Recorder or Saved Models** tab → **Preview** to watch it live. (Offline models are skipped with a note — a dead stream can crash an embedded player.) It opens in an external mpv/VLC/ffplay window by default; choose the engine (Auto/mpv/VLC) and switch to an embedded in-app player under **⚙ Settings → Stream preview**

### Settings (⚙ Settings tab)

All settings live in the **⚙ Settings** tab (the left panel now holds just **Add Model** and the live status panel). Change anything there and click **Save Settings**.

The Settings tab also has a **🔍 System Check** panel that shows whether each external tool/package is found (ffmpeg, ffplay, mpv, VLC, python-vlc, python-mpv, tdjson, Playwright Chromium), and offers one-click fixes — **Add to PATH** for tools that are installed but not on your PATH, and **Install** for the optional Python packages / the Stripchat browser.

| Setting | What it does |
|---|---|
| Output Folder | Where recordings are saved |
| Max File Size (MB) | Split recordings into parts at this size (empty = unlimited) |
| Check Interval (sec) | How often each model's status is polled (default 30) |
| Max Quality (all models) | Global stream-quality cap: Unlimited / 1080p / 720p / 480p. Applied when a recording (re)starts. Right-click a model for a per-model override that beats this |
| Minimize to SysTray | Hide to the tray instead of the taskbar |
| Notifications | Master toggle for Windows toast notifications. *In the new web UI* this lives in its own **Notifications** box with per-type toggles (Recording started/stopped, Dropped segments, Quality downgraded, Low disk space), a toast-duration slider (1–5 s), and a **🌟 VIP List** — add models by right-clicking them, then enable **VIP only** to be notified for just those models |
| ⚠ Dropped-Segment Warnings | Toast when a recording is losing segments due to saturated bandwidth (always logged to the Activity Log regardless). A model simply going offline no longer triggers a false warning |
| ⛔ Stop All on Low Disk Space | Low-disk guard (off by default) with two configurable thresholds: **Stop below (GB free)** (default 20) and **Resume at (GB free)** (default 40). When the drive holding the output folder drops below the stop threshold, every active recording is stopped and no new recording can start (manual REC, auto-rec, restarts and file splits are all blocked) until free space climbs back up to the resume threshold or you uncheck this option. The gap between the two thresholds is deliberate — resuming right at the stop line would let a restarted recording immediately re-trip the guard. Blocked models show `Low disk` as their error; recovery is automatic |
| ⬇ Auto-Downgrade Quality | Restart a stream one quality step lower when it keeps losing segments (≥10 s lost within 60 s). Only that stream is touched; manual per-model quality choices are respected. Not available for Stripchat |
| 🎭 Stripchat Browser Fallback | When Stripchat's browserless native path can't resolve a stream, fall back to the Playwright browser recorder. Enabled by default. Uncheck to record native-only — if native fails the stream is skipped and the browser never launches |
| 🔒 Privacy Mode | Idle screen cover |
| Open links with | Which browser **Open in Browser** uses: *Ask each time*, *System default*, or a specific installed browser. You're prompted the first time (with a *Remember my choice* option); change or reset it here anytime. Right-click → **Open in Browser (choose…)** picks a one-off browser without changing this default |
| Stream preview | How right-click → **Preview** plays a stream. **Mode:** *External window* (standalone player, lowest impact — default) or *Embedded (in-app)*. **Preview engine:** *Auto* / *mpv* / *VLC* (Auto uses whatever's installed; external also falls back to ffplay). Optional **Player path** points at a specific `mpv.exe`/`vlc.exe` |

---

## Output Files

Files are saved to the configured output folder (default: `~/Videos/StreamRecorder`).

**Filename format:**
```
modelname_CB_20240515_143022.ts            ← single file (no split)
modelname_ST_20240515_143022.ts
modelname_CS_20240515_143022_part001.ts    ← split into parts
modelname_CS_20240515_143022_part002.ts
```

- A recording that never hits the **Max File Size** limit is one unsuffixed file. When it splits, every part of that recording shares the same timestamp and is numbered `_part001`, `_part002`, … (3-digit). The same `_partNNN` scheme is used by the Telegram pipeline when it splits a `.mp4` for upload.
- `CB` = Chaturbate, `ST` = Stripchat, `CS` = Camsoda, `MFC` = MyFreeCams
- `.ts` container — plays in VLC, MPV, or any player that supports MPEG-TS
- Convert to `.mp4` with: `ffmpeg -i input.ts -c copy output.mp4`

---

## File Splitting

Set **Max File Size (MB)** in the settings panel. When the current recording file reaches that size, the recorder:
1. Stops the current ffmpeg (or Playwright) process gracefully so the `.ts` trailer flushes
2. Re-fetches the stream URL
3. Starts a new file with the next part number
4. Sends a notification

This keeps individual files manageable while never missing a moment of the stream.

---

## Telegram Upload Pipeline (optional)

The **Output / Upload** tab can convert finished `.ts` recordings to `.mp4` and upload them to a Telegram group/topic automatically.

The pipeline has **two independent stages** you can run alone or together, via the **Stages** checkboxes at the top of the tab:

- **① Convert .ts → .mp4** — converts (and size-splits) finished recordings into `.mp4` files in the converted folder, and *keeps* them. Use this on its own to get `.mp4` files without uploading anywhere. No Telegram setup needed.
- **② Upload .mp4 to Telegram** — uploads `.mp4` files from the converted folder to your Telegram group/topic. Run it together with ① for the full convert-then-upload flow, or on its own to upload `.mp4` files you already have.

**Stand-by model:** the pipeline starts even with *no* stage checked — it simply sits in **● STAND BY** doing nothing. Tick stages at any time and they take effect immediately; the header shows **● CONVERTING**, **● UPLOADING**, or **● CONVERTING & UPLOADING** accordingly. Unchecking a stage stops it after its current task finishes (a conversion or an upload in progress is never interrupted). No stop/restart is ever needed to change stages — including turning Upload on for the first time (it connects to Telegram on demand, reusing your saved session).

Telegram credentials are only required once you enable **②**; if they're missing, the pipeline log says so and Upload stays idle until you fill them in, save, and re-tick Upload.

**First time? Use the wizard.** Click **🧙 Setup Wizard** at the top of the Output / Upload tab for a guided, step-by-step setup — it walks you through the API ID/Hash, the destination group/topic, and optional folders, saves everything, and can start the pipeline for you (you'll be prompted for your phone number and login code on first connect).

To set it up manually instead:

1. Get your `api_id` / `api_hash` from https://my.telegram.org
2. Fill in the **Telegram / Pipeline settings** in the Output / Upload tab (group ID, optional topic ID)
3. Start the pipeline and tick the stages you want, whenever you want

Settings (including the stage choices) are stored in `Pipeline/pipeline_settings.json`. The Upload stage requires the `tdjson` package (installed via `requirements.txt`); the Convert-only stage does not.

---

## Local Control API (port 5200)

While Scr33nX is running it serves a small HTTP API on `http://127.0.0.1:5200`, used by the browser extensions **and** by external automation (e.g. the OpenClaw chat bot — see **[OPENCLAW-HOWTO.md](docs/OPENCLAW-HOWTO.md)**). It is loopback-only (no remote access) and unauthenticated.

| Method & path | Body | Action |
|---|---|---|
| `GET /status` | `?name=&site=` | model state: `in_recorder`, `in_saved`, `status`, `auto`, `rank` |
| `GET /dashboard` | — | aggregate snapshot: per-site (`CB`/`SC`/`CS`/`MFC`) + `all` totals of `total`/`recording`/`online`/`offline` |
| `POST /add` | `{name, site, target}` | add to `recorder` or `saved` |
| `POST /record` | `{name, site, action}` | `start` / `stop` recording one model |
| `POST /auto` | `{name, site, enabled}` | toggle AUTO for a model |
| `POST /rank` | `{name, site, rank}` | set a model's 0–5 star rank (`0` clears); the model must already be in Saved Models or the Recorder |
| `POST /remove` | `{name, site, target}` | remove from `recorder` or `saved` |
| `POST /stop_all` | — | stop every active download + clear all AUTO |
| `POST /clear` | — | stop monitor + all downloads, clear AUTO, remove every Recorder model (Saved kept) |
| `POST /monitor` | `{target, enabled}` | start/stop the `recorder` monitor or `saved` scanner |
| `POST /pipeline` | `{enabled}` | start/stop the pipeline (starts in stand-by) |
| `POST /pipeline/stage` | `{convert?, upload?}` | tick/untick the Convert and/or Upload stages; applies live if running, otherwise on next start |
| `POST /quit` | — | gracefully shut the app down |

A command-line helper, **`src/scr33nx_ctl.py`**, wraps all of these (plus `open`, which launches the app) — run `python src/scr33nx_ctl.py --help` for the subcommands.

---

## How recording works (architecture notes)

> 📄 **Full technical reference:** see **[RecordingLogics.md](docs/RecordingLogics.md)** for the complete, per-site "how to make it work" documentation — resolver protocols, the relay internals, the Stripchat MOUFLON/Playwright paths, the MyFreeCams FCS websocket, exit codes, and a troubleshooting map. That file is written so another engineer (or LLM) can pick up the recording pipeline cold.

- **Local relay** (`src/cb_relay.py`): ffmpeg never talks to the CDN directly. All HLS traffic goes through a relay on `127.0.0.1` which:
  - pins the **highest-bitrate variant within your quality cap** (per-model override → global setting → unlimited) so quality can't silently change mid-recording,
  - **prefetches upcoming segments in parallel** (64 shared workers, in-memory cache) so ffmpeg is served instantly and falling behind the live window — the cause of 1–2 s timestamp jumps — is avoided,
  - survives the CDN's mid-segment TLS resets that corrupt direct ffmpeg downloads,
  - detects segments that expired before they could be downloaded and reports them (Activity Log + optional notification),
  - feeds the **bandwidth meter** (counts every byte fetched upstream).
- **Chaturbate / Camsoda**: public HLS resolver → relay → `ffmpeg -c copy`
- **MyFreeCams**: no public API — a guest login over MFC's FCS websocket (`src/mfc.py`) resolves the model's video state + HLS edge, then relay → `ffmpeg -c copy`
- **Stripchat**: native MOUFLON-decrypting path through the relay (no browser) when possible; otherwise a Playwright-driven Chromium session (`src/stripchat_live.py`) writes the MPEG-TS directly. *Note: the browser fallback doesn't pass through the relay, so it isn't counted by the bandwidth meter.*
- **Extension API**: the app serves a small local HTTP API on **port 5200** used by the browser extensions

---

## Notes

- The app polls each model every N seconds (configurable, default 30s)
- If a model is in a private show or temporarily offline, the app keeps checking and resumes when they go public/online
- All settings are saved automatically when you click "💾 Save Settings". Models, AUTO flags, Saved Models and **star ranks** are also saved to disk **immediately on every change** (including changes made from the browser extension), so an abrupt close doesn't lose them
- **Run only one Scr33nX at a time.** Two instances share the same settings file and overwrite each other's models/ranks. If you launch a second one it now warns you at startup (the control port is taken) and offers to close itself — accept unless you really know what you're doing
- Changing or clearing an **existing** star rank by clicking the RANK column asks for confirmation first (misclick guard, app only); rating an unranked model stays one-click, and the browser extension is never prompted
- If many simultaneous recordings drop segments (watch for ⚠ warnings), your total internet bandwidth is the limit. Each 1080p stream needs roughly 5–6 Mbps sustained. In order of preference: set a global **Max Quality** cap (720p roughly halves usage vs. unlimited), enable **⬇ Auto-Downgrade** so only struggling streams lose quality, or record fewer models at once.
- For bug reports, attach `streamrecorder.log` (in `%LOCALAPPDATA%\Scr33nX`) — it contains everything the Activity Log shows plus ffmpeg/relay internals, from either interface (they share the same log file).
