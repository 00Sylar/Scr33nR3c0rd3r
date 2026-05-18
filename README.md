# StreamRecorder v2

A lightweight Windows desktop app for automatically recording live streams from **Chaturbate**, **Stripchat**, and **Camsoda**.

v2 adds Stripchat support (via Playwright) and Camsoda support on top of the original Chaturbate recorder, plus a Telegram upload pipeline.

---

## Features

- ✅ Auto-detects when a model goes online and starts recording immediately
- ✅ Record multiple models simultaneously across Chaturbate, Stripchat, and Camsoda
- ✅ Configurable output folder and filename (includes model name, site, timestamp, part number)
- ✅ **File splitting** — automatically starts a new file when the recording reaches your defined max size (e.g. 3070 MB)
- ✅ Windows desktop notifications (recording started/stopped/split)
- ✅ Clean dark GUI — no terminal needed
- ✅ Settings saved between sessions (models list, output folder, max size, etc.)
- ✅ Activity log with timestamps
- ✅ Chromium extension — one-click add from a model's page (port 5200)
- ✅ Telegram upload pipeline (optional)

---

## Requirements

- **Python 3.10+** — https://python.org (check "Add to PATH" during install)
- **ffmpeg** — https://ffmpeg.org/download.html
- **Playwright Chromium** (for Stripchat) — installed automatically by `playwright install chromium`

---

## Setup

### 1. Install Python
Download from https://python.org. During install, **check "Add Python to PATH"**.

### 2. Install ffmpeg
- Download a Windows build from https://www.gyan.dev/ffmpeg/builds/ (grab `ffmpeg-release-essentials.zip`)
- Extract it and place `ffmpeg.exe` in the `StreamRecorder` folder
  **OR** add ffmpeg to your system PATH so it's available globally.

### 3. Install Python dependencies
```
pip install -r requirements.txt
playwright install chromium
```

### 4. Run the app
Double-click `StreamRecorder.bat`

---

## Usage

1. **Add models** using the left panel — enter the username (or paste a full model URL) and select the site: `chaturbate`, `stripchat`, or `camsoda`
2. Click **▶ START MONITORING** — the app polls each model at the configured interval
3. When a model goes live, recording starts automatically and you'll get a notification
4. When the model goes offline, the recording stops automatically
5. If you set a **Max File Size**, the recording splits into numbered parts (e.g. `_part001`, `_part002`) automatically

---

## Output Files

Files are saved to the configured output folder (default: `~/Videos/StreamRecorder`).

**Filename format:**
```
modelname_CB_20240515_143022_part001.ts
modelname_ST_20240515_143022.ts
modelname_CS_20240515_143022_part001.ts
```

- `CB` = Chaturbate, `ST` = Stripchat, `CS` = Camsoda
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

## Site Notes

- **Chaturbate / Camsoda**: public HLS resolver + ffmpeg `-c copy`
- **Stripchat**: Playwright-driven Chromium session (`stripchat_live.py`) writes an MPEG-TS directly — no login required for public rooms
- **Extension**: the bundled Chromium extension posts to the local API on **port 5200**

---

## Notes

- The app polls each model every N seconds (configurable, default 30s)
- If a model is in a private show or temporarily offline, the app keeps checking and resumes when they go public/online
- All settings are saved automatically when you click "Save Settings" or close the app
