# Scr33nX — assistant working notes

Windows desktop app (Tkinter) that auto-records live streams from Chaturbate,
Stripchat, Camsoda, and MyFreeCams through a local HLS relay. Key files:
`app.py` (GUI + local API on port 5200), `recorder.py` (engine), `cb_relay.py`
(relay), `settings.py`, `mfc.py`/`stripchat_*` (resolvers), `extension/` (Chrome
+ Firefox popups), `scr33nx_ctl.py` (OpenClaw bot control).

## Documentation discipline — required

**Every functional change must update its docs in the same change.** Full rules
and the "if you change X, update Y" map are in **[CONTRIBUTING.md](CONTRIBUTING.md)** —
follow it. The short version:

- **API endpoint** (`app.py` `_ApiHandler`) → README *Local Control API* table + `OPENCLAW-HOWTO.md` (if bot-relevant) + CHANGELOG.
- **UI feature / setting / extension change** → README *Features* / *Usage* / *Settings* + CHANGELOG.
- **Recording internals** (relay/resolvers/ffmpeg) → `RercordingLogics.md` + CHANGELOG.
- **New dependency** → `requirements.txt` + README *Requirements* + CHANGELOG.

Always add a `CHANGELOG.md` entry under `## [Unreleased]` (`Added`/`Changed`/`Fixed`),
written for users. Do this **without being asked** as part of the change.

## Releases

Don't bump versions per change — work accumulates under `[Unreleased]`. When asked
to "cut vX.Y": move `[Unreleased]` into `## VX.Y — <date>`, reset `[Unreleased]`,
commit, then `git tag -a vX.Y -m "..."` and `git push origin main vX.Y`. Never
move/delete an already-pushed tag.

## Conventions / gotchas

- **Never call a `messagebox`/modal from the API path or any background-triggered
  code** — a modal blocks the Tk event loop and freezes the app (see the Privacy
  Mode and API-handler history). Use the no-dialog `_api_*` variants.
- Keep the two extension popups in sync — `extension/Chromium/popup.js` and
  `extension/Firefox/popup.js` differ **only** in `chrome.` vs `browser.` lines.
- Validate before finishing: `python -m py_compile` changed Python; `node --check`
  both `popup.js` files.
- Single instance only: the API binds port 5200; a second instance can't and runs
  half-working.

## Workflow

The user (solo dev, Pro plan — keep token use lean) tests manually and then says
"push to main." Branch is `main`; push only when asked. End commit messages with
the `Co-Authored-By` trailer.
