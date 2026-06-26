# Features

## Recording

- **Auto‑detect online** — starts recording the moment a model goes live, stops
  when they go offline.
- **Multi‑site, simultaneous** — record many models at once across Chaturbate,
  Stripchat, Camsoda, and MyFreeCams.
- **Highest‑quality pinning** — the relay locks ffmpeg to the top‑bitrate
  variant so quality can't silently fall back mid‑recording.
- **Quality caps** — a global *Max Quality* setting (Unlimited / 1080p / 720p /
  480p) plus per‑model overrides (right‑click a model). Per‑model beats global.
- **Auto‑downgrade (optional)** — a stream that persistently loses segments is
  restarted one step lower (720p → 480p → 240p) without touching other
  recordings; resets when that model's session ends. Models with a manual
  override are never auto‑downgraded.
- **Parallel segment prefetching** — segments are fetched ahead of ffmpeg in
  parallel (64 shared workers), so slow streams catch up instead of skipping
  1–2 s chunks. Built for dozens of concurrent recordings.
- **File splitting** — automatically starts a new numbered file when a recording
  hits your Max File Size (e.g. 3070 MB).
- **Stripchat native path** — records browserless (MOUFLON) when possible, with
  automatic Playwright/Chromium fallback.

See [[Recording Internals|Recording-Internals]] for how all of this works.

## Monitoring & UI

- Clean black & red dark GUI — no terminal needed.
- **Version number** in the header next to the logo, plus a background
  **update check** against GitHub (see [[Installation]]).
- **Live bandwidth meter** in the header (`↓` download / `↑` Telegram upload) —
  your signal for when you're approaching your connection's limit.
- **Log file** — everything (Activity Log, ffmpeg stderr, relay warnings, crash
  tracebacks) is also written to `%LOCALAPPDATA%\Scr33nX\streamrecorder.log`
  (rotating, 5 MB × 3).
- **Dropped‑segment warnings** — get notified when a stream is losing segments
  because bandwidth can't keep up (toggle in [[Settings]]).
- **Saved Models** tab — view‑only watchlist with online/offline status.
- **⭐ 1–5 star ranks** — rate any model on the Recorder or Saved Models tab
  (click a star, click again to clear; or right‑click → **Set Rank** for a whole
  selection). Sortable **RANK** column, shared per‑model across both tabs and the
  extension, saved between sessions.
- Windows desktop notifications (started/stopped/split/dropped segments).
- Minimize to system tray.
- 🔒 **Privacy Mode** — idle screen cover.
- Activity log with timestamps; settings saved between sessions.

## Integrations

- **Browser extension** (Chromium **and** Firefox) — one‑click add from a
  model's page, plus 1–5 star rating right from the popup. See
  [[Browser Extension|Browser-Extension]].
- **Telegram upload pipeline** (optional) — converts finished recordings and
  uploads them to a Telegram group/topic. See
  [[Telegram Pipeline|Telegram-Pipeline]].
- **Chat‑bot / agent control (OpenClaw)** — drive Scr33nX from your phone over
  Telegram/WhatsApp. See [[OpenClaw Bot|OpenClaw-Bot]].
