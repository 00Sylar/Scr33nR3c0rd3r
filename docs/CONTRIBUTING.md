# Contributing to Scr33nX

This file is the **definition of done** for any change to Scr33nX. The goal is
simple: the code and the documentation never drift apart. If a change alters what
the app *does* or *how it's used*, the matching docs are updated **in the same
change** — not "later."

> Working with an AI assistant (Claude)? This rule is also mirrored in
> [`CLAUDE.md`](../CLAUDE.md), which the assistant reads automatically, so docs get
> updated without being asked.

---

## The one rule

**No functional change ships without its documentation.** A feature, setting,
API endpoint, file format, or user-visible behavior that changes must be
reflected in the relevant docs and in the changelog, as part of the same commit
(or PR).

---

## Documentation map — "if you change X, update Y"

| If you change… | Update… |
|---|---|
| A **local-API endpoint** (`src/app.py` `_ApiHandler`) | `../README.md` → *Local Control API* table · `OPENCLAW-HOWTO.md` (if the bot should use it: §2 command map, §3 script table, and add the `src/scr33nx_ctl.py` command) · `CHANGELOG.md` |
| A **UI feature / tab / right-click action** | `../README.md` → *Features* and/or *Usage* · `CHANGELOG.md` |
| A **Settings option** (checkbox/field) | `../README.md` → *Settings (left panel)* table · `CHANGELOG.md` |
| The **browser extension** (popup/behavior) | `../README.md` → *Features* / extension-install section · `CHANGELOG.md` |
| **Recording internals** (relay, resolvers, ffmpeg, exit codes) | `RecordingLogics.md` · `CHANGELOG.md` |
| **Output filename / file format** | `../README.md` → *Output Files* · `CHANGELOG.md` |
| A **persisted config field** (e.g. `~/.streamrecorder_config.json`, `Pipeline/pipeline_settings.json`) | Note it in the relevant README/CHANGELOG entry so users know what's stored |
| A **new dependency** | `requirements.txt` · `../README.md` → *Requirements* / *Installation* · `CHANGELOG.md` |
| The **OpenClaw bot wiring** (`src/scr33nx_ctl.py`, AGENTS.md flow) | `OPENCLAW-HOWTO.md` · `CHANGELOG.md` |

When in doubt: if a user or another developer could be surprised by the change,
document it.

> Tip: the OpenClaw "muscles → hands → brain" loop (expose API → add
> `src/scr33nx_ctl.py` command → describe in `AGENTS.md`) is documented in
> [`OPENCLAW-HOWTO.md` §6](OPENCLAW-HOWTO.md). Touching one of those three almost
> always means touching the other two.

---

## Changelog conventions

`CHANGELOG.md` loosely follows [Keep a Changelog](https://keepachangelog.com/).

- New work goes under **`## [Unreleased]`**, grouped into **`### Added`**,
  **`### Changed`**, **`### Fixed`** (only the sections you need).
- Write entries for **users**, not commit-by-commit — describe the behavior and,
  for fixes, the symptom and the cause.
- Keep the newest content at the top.

## Cutting a release

When a batch of `[Unreleased]` work is ready to be a version:

1. **Move** the `[Unreleased]` entries into a new dated version section, e.g.
   `## V1.4 — YYYY-MM-DD`, and reset `[Unreleased]` to empty.
2. **Bump `APP_VERSION`** in `src/app.py` to match the new tag (e.g. `"1.4"`). This
   is what shows in the header and what the update checker compares against.
3. **Commit** that change.
4. **Tag** it and push:
   ```
   git tag -a v1.4 -m "Scr33nX V1.4 — <one-line summary>"
   git push origin main v1.4
   ```
5. **Publish the GitHub Release** (on github.com) → Releases → **Draft a new
   release** → choose the `v1.4` tag → paste that version's changelog section →
   **Publish**. *Not optional:* the in-app update checker reads the latest
   **published release** via the GitHub API — a pushed tag alone won't trigger it.
   (Equivalent CLI: `gh release create v1.4 --notes-from-tag`.)

Versioning follows the existing cadence: bump the **minor** (`V1.3 → V1.4`) for a
feature batch, the **patch** (`V1.3 → V1.3.1`) for a fix-only release. Never move
or delete a tag that's already pushed — cut a new one instead.

---

## Before you commit — quick checklist

- [ ] Code change works (the author tests manually).
- [ ] Docs updated per the map above.
- [ ] `CHANGELOG.md` `[Unreleased]` has an entry.
- [ ] If the API changed: README API table **and** (if bot-relevant) `OPENCLAW-HOWTO.md`.
- [ ] `python -m py_compile <changed src/*.py files>` passes; both extension
      `popup.js` files validate and stay in sync (only the `chrome`/`browser`
      lines differ).
