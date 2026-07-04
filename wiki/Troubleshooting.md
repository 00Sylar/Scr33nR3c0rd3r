# Troubleshooting

> When reporting a problem, attach `streamrecorder.log` (in
> `%LOCALAPPDATA%\Scr33nX`) — it contains everything the Activity Log shows plus
> ffmpeg/relay internals.

## App / general

| Symptom | Cause / fix |
|---|---|
| Tray icon controls the "wrong" app, extension does nothing | A **second instance** was running. Current builds refuse to start a second instance ("You can only open one instance of this app"); on older builds, close the extras — only one Scr33nX can bind port 5200. |
| Window looks frozen | If it happened around Privacy Mode, update — older builds could pop a modal hidden behind the cover. Otherwise check the log for a stalled subprocess. |
| Update indicator never appears | The check needs a **published GitHub Release**, not just a tag. If no releases are published, nothing triggers. It also fails silently when offline. |

## Recording / quality

| Symptom | Cause / fix |
|---|---|
| Streams drop segments / ⚠ warnings | Your total bandwidth is the limit (~5–6 Mbps per 1080p stream). Set a global **Max Quality** cap (720p ≈ half the usage), enable **⬇ Auto‑Downgrade**, or record fewer models at once. |
| Recording quality changed mid‑stream | Shouldn't happen — the relay pins the top variant within your cap. If it does, capture the log and the model/site. |
| Stripchat won't record | If **Browser Fallback** is off and the native path fails, the stream is skipped by design. Enable the fallback in [[Settings]] (needs `playwright install chromium`). |
| Bandwidth meter shows nothing for a Stripchat recording | The Playwright browser fallback doesn't pass through the relay, so it isn't counted. That's expected. |
| `.ts` won't play | Use VLC/MPV, or convert: `ffmpeg -i input.ts -c copy output.mp4`. |

## Extension

| Symptom | Cause / fix |
|---|---|
| Popup buttons do nothing | Scr33nX must be running and listening on `localhost:5200`. Confirm only one instance is open. |
| Star rating is disabled in the popup | By design — a model must be in **Saved Models or the Recorder** before it can be ranked (prevents orphan ranks). Add it first. |

## OpenClaw bot

See the full table in `docs/OPENCLAW-HOWTO.md`. Quick hits:

| Symptom | Cause / fix |
|---|---|
| Bot: *"Is the app running?"* | Scr33nX is closed → say *"open Scr33nX"*. |
| New command does nothing / 404 | Restart Scr33nX after an `app.py` change. |
| Bot ignores a new phrasing | Send `/new` after editing `AGENTS.md`. |
| Bot: *"Unknown model"* | Model id missing the `provider/` prefix in `openclaw.json`. |
| Bot: *"out of extra usage"* | It's on the direct‑API runtime — switch to the **Claude CLI** runtime. |
| Bot: *"Provider … in cooldown (billing)"* | Stale circuit‑breaker → `openclaw gateway restart`. |
| `close` seems to hang | It flushes active recordings first (can take ~20 s with many). Normal. |

Still stuck? Open an issue on the
[repository](https://github.com/00Sylar/Scr33nX) with the relevant log excerpt.
