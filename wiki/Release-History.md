# Release History

This is a readable summary. The authoritative, full changelog is `docs/CHANGELOG.md`
in the repository, and each version is also a
[GitHub Release](https://github.com/00Sylar/Scr33nX/releases).

---

## V1.4 — 2026‑06‑26

First version published as an official GitHub Release; the in‑app update check
goes live from here.

**Added**
- **Version number + automatic update check** in the header — shows the running
  build and flags when a newer GitHub release exists.
- **⛔ Force Quit / Terminate** — header button + tray "Force Quit" item that
  hard‑kills the whole process tree (ffmpeg, relay, Chromium) like Task Manager's
  *End Task*; confirms only when a recording is active.
- **Rank models from the OpenClaw bot** — `rank <model> 0‑5`, plus `--rank N` on
  `add‑saved` / `add‑recorder` / `record`.

**Changed**
- **Cleaner repo layout** — source under `src/`, docs under `docs/`. No change to
  how you run it (double‑click `StreamRecorder.bat`).

**Fixed**
- **Blank taskbar icon** — the `.ico` is now pushed to the taskbar via
  `WM_SETICON`.
- **Stale ranks** no longer linger after a model leaves both lists.

## V1.3 — 2026‑06‑24

**Added**
- **⭐ 1–5 star model ranks** on both the Recorder and Saved Models tabs —
  sortable **RANK** column, keyed by model identity (same stars everywhere,
  including the extension), persisted between sessions. Saved‑Models export now
  carries ranks and merges instead of overwriting; import back‑fills stars.
- **Rank from the browser extension** — a star row in the popup, enabled once
  the model is in Saved Models or the Recorder. New `POST /rank`; `GET /status`
  now also returns `rank`.
- **Live polling in the extension popup** — status/rank/membership update in
  place while open.
- **Status dashboard (left panel)** — per‑site breakdown (🟡 CB, 🔴 SC, 🔵 CS,
  🟢 MFC) with total / recording / online / offline, plus an ALL summary.

**Fixed**
- Privacy Mode could freeze the app (modal hidden behind the cover) — now an
  on‑cover Exit/Stay panel, no modal.
- UI freeze when starting/stopping many recordings at once — launches now go
  through a bounded pool (4 concurrent) with per‑model dedupe.
- Silent duplicate‑instance — the failed port bind is now logged and surfaced.
- Saved Models desync between the API and the lazy‑built UI rows.

## V1.2 — 2026‑06‑13

**Added**
- **🧹 CLEAR RECORDER** button (and `POST /clear` + `scr33nx_ctl.py clear`).
- **Bot dashboard + clear (OpenClaw)** — `GET /dashboard`, wired into the script.
- **🎭 Stripchat Browser Fallback toggle** in Settings.
- **Chat‑bot / agent control via the local API (OpenClaw)** — `scr33nx_ctl.py`
  wraps every action; full walkthrough in `docs/OPENCLAW-HOWTO.md`.
- **New API endpoints** — `POST /stop_all`, `/monitor`, `/pipeline`, `/quit`;
  `POST /remove` gained `target: "saved"`.

**Changed**
- **Standardized output filenames** — no part suffix until a split happens, then
  `_part001`… with one shared timestamp; pipeline `.mp4` splits use the same
  3‑digit padding.

**Fixed**
- **STOP TELEGRAM PIPELINE** now halts the uploader workers (was draining the
  backlog).

## V1.1 — 2026‑06‑12

**Added**
- **OneTab / browser integration** — "Open in Browser" and "Copy as OneTab List".
- **Status filter** on both tabs (any combination of statuses; live updates).
- **Row checkboxes** on both tabs — an explicit working set for every bulk
  action, with "Check All Visible"/"Uncheck All".
- **Faster multi‑selection** — press‑and‑drag range select, Shift/Ctrl click.
- **Saved‑tab bulk actions** — "Add to Recorder (N)" and "Remove from Saved (N)".

## V1.0 — 2026‑06‑11

**Added**
- **Filter boxes** on the Recorder and Saved Models tabs.
- **Lazy Saved Models tab** — rows built on first visit; engine registration on
  scanner start; status mirroring preserved.

**Fixed**
- **Tray‑icon hard crashes** (64‑bit `restype` declarations; single persistent
  WNDPROC; non‑blocking tray creation).
- **Recordings stalled after the first file split** — every launch path now
  drains ffmpeg stderr.

---

> Newer, not‑yet‑released changes accumulate under `## [Unreleased]` in
> `docs/CHANGELOG.md`.
