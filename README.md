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
- ✅ Clean black & red dark GUI — no terminal needed
- ✅ **Live bandwidth meter** in the header (`↓ X.X Mbps` download / `↑` Telegram upload) showing Scr33nX's total traffic — your indicator for when you're approaching your internet connection's limit
- ✅ **Beta log file** — everything (Activity Log, ffmpeg stderr, relay warnings, crash tracebacks) is also written to `streamrecorder.log` next to the app (rotating, 5 MB × 3)
- ✅ **Dropped-segment warnings** — get notified when a stream is losing segments because bandwidth can't keep up (toggle in Settings)
- ✅ **Saved Models** tab — view-only watchlist with online/offline status
- ✅ Windows desktop notifications (recording started/stopped/split/dropped segments)
- ✅ Minimize to system tray
- ✅ 🔒 Privacy Mode — idle screen cover
- ✅ Activity log with timestamps
- ✅ Settings saved between sessions (models, output folder, max size, etc.)

### Integrations
- ✅ Browser extension (Chromium **and** Firefox) — one-click add from a model's page (local API, port 5200)
- ✅ Telegram upload pipeline (optional) — converts finished recordings and uploads them to a Telegram group/topic

---

> 📜 See **[CHANGELOG.md](CHANGELOG.md)** for a running history of changes, and **[RercordingLogics.md](RercordingLogics.md)** for the deep technical recording reference.

---

## Requirements

- **Windows 10/11**
- **Python 3.10+** — https://python.org (check "Add Python to PATH" during install)
- **ffmpeg** — https://ffmpeg.org/download.html
- **Playwright Chromium** — only needed as the Stripchat fallback recorder

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
Double-click **`StreamRecorder.bat`** (launches the GUI with no console window).

### 5. Install the browser extension (optional)

The extension adds models to Scr33nX with one click while you're on their page. The app must be running (it listens on `http://localhost:5200`).

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

1. **Add models** using the left panel — enter the username (or paste a full model URL; the site is auto-detected) and select the site: `chaturbate`, `stripchat`, `camsoda`, or `myfreecams`
2. Click **▶ START MONITOR** — the app polls each model at the configured interval
3. When a model goes live, recording starts automatically and you'll get a notification
4. When the model goes offline, the recording stops automatically
5. If you set a **Max File Size**, the recording splits into numbered parts (e.g. `_part001`, `_part002`) automatically
6. Watch the **↓ Mbps** meter in the header while recording several models — if your streams start dropping segments you'll also get a warning notification

### Settings (left panel)

| Setting | What it does |
|---|---|
| Output Folder | Where recordings are saved |
| Max File Size (MB) | Split recordings into parts at this size (empty = unlimited) |
| Check Interval (sec) | How often each model's status is polled (default 30) |
| Max Quality (all models) | Global stream-quality cap: Unlimited / 1080p / 720p / 480p. Applied when a recording (re)starts. Right-click a model for a per-model override that beats this |
| Minimize to SysTray | Hide to the tray instead of the taskbar |
| Notifications | Master toggle for Windows toast notifications |
| ⚠ Dropped-Segment Warnings | Toast when a recording is losing segments due to saturated bandwidth (always logged to the Activity Log regardless) |
| ⬇ Auto-Downgrade Quality | Restart a stream one quality step lower when it keeps losing segments (≥10 s lost within 60 s). Only that stream is touched; manual per-model quality choices are respected. Not available for Stripchat |
| 🔒 Privacy Mode | Idle screen cover |

---

## Output Files

Files are saved to the configured output folder (default: `~/Videos/StreamRecorder`).

**Filename format:**
```
modelname_CB_20240515_143022_part001.ts
modelname_ST_20240515_143022.ts
modelname_CS_20240515_143022_part001.ts
modelname_MFC_20240515_143022.ts
```

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

1. Get your `api_id` / `api_hash` from https://my.telegram.org
2. Fill in the **Telegram / Pipeline settings** in the Output / Upload tab (group ID, optional topic ID)
3. Save and enable the pipeline

Settings are stored in `Pipeline/pipeline_settings.json`. Requires the `tdjson` package (installed via `requirements.txt`).

---

## How recording works (architecture notes)

> 📄 **Full technical reference:** see **[RercordingLogics.md](RercordingLogics.md)** for the complete, per-site "how to make it work" documentation — resolver protocols, the relay internals, the Stripchat MOUFLON/Playwright paths, the MyFreeCams FCS websocket, exit codes, and a troubleshooting map. That file is written so another engineer (or LLM) can pick up the recording pipeline cold.

- **Local relay** (`cb_relay.py`): ffmpeg never talks to the CDN directly. All HLS traffic goes through a relay on `127.0.0.1` which:
  - pins the **highest-bitrate variant within your quality cap** (per-model override → global setting → unlimited) so quality can't silently change mid-recording,
  - **prefetches upcoming segments in parallel** (64 shared workers, in-memory cache) so ffmpeg is served instantly and falling behind the live window — the cause of 1–2 s timestamp jumps — is avoided,
  - survives the CDN's mid-segment TLS resets that corrupt direct ffmpeg downloads,
  - detects segments that expired before they could be downloaded and reports them (Activity Log + optional notification),
  - feeds the **bandwidth meter** (counts every byte fetched upstream).
- **Chaturbate / Camsoda**: public HLS resolver → relay → `ffmpeg -c copy`
- **MyFreeCams**: no public API — a guest login over MFC's FCS websocket (`mfc.py`) resolves the model's video state + HLS edge, then relay → `ffmpeg -c copy`
- **Stripchat**: native MOUFLON-decrypting path through the relay (no browser) when possible; otherwise a Playwright-driven Chromium session (`stripchat_live.py`) writes the MPEG-TS directly. *Note: the browser fallback doesn't pass through the relay, so it isn't counted by the bandwidth meter.*
- **Extension API**: the app serves a small local HTTP API on **port 5200** used by the browser extensions

---

## Notes

- The app polls each model every N seconds (configurable, default 30s)
- If a model is in a private show or temporarily offline, the app keeps checking and resumes when they go public/online
- All settings are saved automatically when you click "💾 Save Settings"
- If many simultaneous recordings drop segments (watch for ⚠ warnings), your total internet bandwidth is the limit. Each 1080p stream needs roughly 5–6 Mbps sustained. In order of preference: set a global **Max Quality** cap (720p roughly halves usage vs. unlimited), enable **⬇ Auto-Downgrade** so only struggling streams lose quality, or record fewer models at once.
- For bug reports during beta, attach `streamrecorder.log` (next to `app.py`) — it contains everything the Activity Log shows plus ffmpeg/relay internals.
