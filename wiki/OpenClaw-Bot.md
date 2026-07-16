# OpenClaw Bot — control Scr33nX from your phone

Scr33nX can be driven from a messaging app (Telegram/WhatsApp) through an
**OpenClaw** agent. You text the bot in plain language; it maps your intent to a
command and runs it on your PC through the [[Local Control API|Local-Control-API]].

```
You (Telegram) ─▶ OpenClaw bot ─▶ scr33nx_ctl.py ─▶ Scr33nX API (port 5200) ─▶ the app does it
  "record this"   (reads message)   (one cmd/action)   (loopback only)
```

Three moving parts: the **API** (`app.py`, port 5200), the **control script**
(`scr33nx_ctl.py`), and the **bot's instructions** (`AGENTS.md`).

> The complete, authoritative setup — including the exact working OpenClaw
> config and every gotcha — is in **`docs/OPENCLAW-HOWTO.md`** in the repository. This
> page is a quick reference.

## What you can ask

| You say… | It runs | Effect |
|---|---|---|
| "record this: `<link>`" / "grab her" | `record <link>` | add + AUTO on + start recording |
| "just watch for her, she's not live" | `record <link> --auto-only` | add + AUTO on, don't force‑start |
| "add her but don't record yet" | `record <link> --no-auto` | add + record now, AUTO off |
| "stop her" / "stop `<name>`" | `stop <name>` | stop one recording |
| **"stop everything"** | `stop-all` | stop all downloads + clear all AUTO |
| **"clear the recorder"** | `clear` | pause **both** monitors, force-stop all downloads, clear AUTO, remove every model (Saved list kept; scanner paused) |
| **"dashboard status"** / "how many are live?" | `dashboard` | per‑site + totals: total / recording / online / offline |
| "save her" / "add to saved" | `add-saved <link>` | add to Saved Models |
| "save her and rank 5" | `add-saved <link> --rank 5` | add to Saved **and** set the star rank |
| "rank her 4" / "give `<name>` 3 stars" | `rank <name> 4` | set a 1–5 star rank (must already be on a list) |
| "clear her rank" | `rank <name> 0` | clear the rank |
| "remove her from saved" | `remove-saved <name>` | remove from Saved Models |
| "remove her from the recorder" | `remove <name>` | remove from Recorder |
| "auto on/off for `<name>`" | `auto <name> on` / `off` | toggle AUTO |
| "is she recording?" | `status <name>` | report her state |
| "start/stop the monitor" | `monitor recorder on` / `off` | recorder monitor |
| "start/stop the scanner" | `monitor saved on` / `off` | saved‑models scanner |
| "start/stop the pipeline" | `pipeline on` / `off` | Telegram upload pipeline (starts in stand‑by) |
| "turn on/off pipeline convert" | `pipeline convert on` / `off` | tick/untick the Convert stage, live |
| "turn on/off pipeline upload" | `pipeline upload on` / `off` | tick/untick the Upload stage, live |
| "open / launch Scr33nX" | `open` | start the app |
| "close / quit Scr33nX" | `close` | gracefully shut it down |

**Supported sites:** Chaturbate, Stripchat, Camsoda, MyFreeCams. Paste a full URL
(site auto‑detected, including the MyFreeCams `#name` format) or send a bare
username.

## The one rule

**Scr33nX must be running** for everything except `open`. If you text a command
while it's closed, the bot replies *"Is the app running?"* — say *"open Scr33nX"*
and retry. `close` stops all recording, so the bot checks with you first if
anything is actively recording.

## After changes

Whenever the API code (`app.py`) or the bot instructions (`AGENTS.md`) change:

1. **Restart Scr33nX** so new endpoints load.
2. In Telegram, send **`/new`** so the bot reloads `AGENTS.md`.
3. Try a command.

For provider/model setup, the Claude‑CLI runtime, billing pools, and the full
troubleshooting table, see **`docs/OPENCLAW-HOWTO.md`** in the repo.
