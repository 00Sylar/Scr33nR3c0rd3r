# Changelog

All notable changes to **Scr33nX** are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Dates are `YYYY-MM-DD`. This project does not yet use formal version numbers, so
entries are grouped by date / milestone.

---

## [Unreleased]

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
