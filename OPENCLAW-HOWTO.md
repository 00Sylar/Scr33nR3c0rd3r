# Controlling Scr33nX from your phone with OpenClaw

This document explains how Scr33nX is wired to **OpenClaw** so you can text a bot
(on Telegram/WhatsApp) commands like *"record this link"*, *"stop everything"*, or
*"open Scr33nX"*, and it does it on your PC. It also records the exact setup that
made it work, the gotchas hit along the way, and how to teach the bot new tricks.

> **TL;DR** — Scr33nX runs a tiny local API (port 5200). A small script
> (`scr33nx_ctl.py`) calls that API. OpenClaw (an AI agent running on your PC)
> reads your chat message, decides which command to run, and runs the script.
> You never touch a terminal once it's set up.

---

## 1. The big picture

```
  You (Telegram) ──▶ OpenClaw bot ──▶ scr33nx_ctl.py ──▶ Scr33nX local API ──▶ the app does it
   "record this"      (Claude reads      (one command       (port 5200,            (adds model,
                       your message)      per action)         loopback only)         records, etc.)
```

Three moving parts, each in its own place:

| Layer | What it is | Where it lives |
|---|---|---|
| **The muscles** | Scr33nX's local API — the actions the app can perform | `app.py` (the `_ApiHandler` class, port 5200) |
| **The hands** | A command-line script that calls the API | `scr33nx_ctl.py` (in the Scr33nX folder) |
| **The brain** | The bot's instructions — when to run what | `C:\Users\luiis\.openclaw\workspace\AGENTS.md` |

To add a new ability you touch all three: expose it in the API, add a script
command, and describe it in `AGENTS.md`. (See [§6](#6-how-to-teach-the-bot-new-commands).)

---

## 2. What you can ask the bot to do

Just talk normally. The bot maps your intent to a command:

| You say… | It runs | Effect |
|---|---|---|
| "record this: `<link>`" / "grab her" | `record <link>` | add + AUTO on + start recording |
| "just watch for her, she's not live" | `record <link> --auto-only` | add + AUTO on, don't force-start |
| "add her but don't record yet" | `record <link> --no-auto` | add + record now, AUTO off |
| "stop her" / "stop `<name>`" | `stop <name>` | stop one recording |
| **"stop everything"** | `stop-all` | stop all downloads + clear all AUTO |
| "save her" / "add to saved" | `add-saved <link>` | add to Saved Models |
| "remove her from saved" | `remove-saved <name>` | remove from Saved Models |
| "remove her from the recorder" | `remove <name>` | remove from Recorder |
| "add to recorder (don't record)" | `add-recorder <link>` | add only |
| "auto on/off for `<name>`" | `auto <name> on` / `off` | toggle AUTO |
| "is she recording?" | `status <name>` | report her state |
| "start/stop the monitor" | `monitor recorder on` / `off` | recorder monitor |
| "start/stop the scanner" | `monitor saved on` / `off` | saved-models scanner |
| "start/stop the pipeline" | `pipeline on` / `off` | Telegram upload pipeline |
| "open / launch Scr33nX" | `open` | start the app |
| "close / quit Scr33nX" | `close` | gracefully shut it down |

**Supported sites:** Chaturbate, Stripchat, Camsoda, MyFreeCams. Paste a full URL
(the site is auto-detected, including the MyFreeCams `#name` format) or send a
bare username — for a bare username the bot adds `--site <site>`.

### The one rule
**Scr33nX must be running** for everything except `open`. If you send a command
while it's closed, the bot will tell you *"Is the app running?"* — just say
*"open Scr33nX"* (or open it yourself) and retry. `close` stops all recording, so
the bot will check with you first if anything is actively recording.

---

## 3. The control script (`scr33nx_ctl.py`)

A self-contained Python script in the Scr33nX folder. The bot runs it; you can
run it by hand too. It prints one line of JSON per call.

```
python scr33nx_ctl.py <command> [args]
```

| Command | Example |
|---|---|
| `status <model> [--site S]` | `python scr33nx_ctl.py status someone --site stripchat` |
| `record <model> [--site S] [--auto-only] [--no-auto]` | `python scr33nx_ctl.py record "https://chaturbate.com/x/"` |
| `stop <model> [--site S]` | `python scr33nx_ctl.py stop x --site chaturbate` |
| `add-recorder` / `add-saved <model>` | `python scr33nx_ctl.py add-saved "stripchat.com/x"` |
| `remove` / `remove-saved <model>` | `python scr33nx_ctl.py remove-saved x --site camsoda` |
| `auto <model> on|off` | `python scr33nx_ctl.py auto x on --site chaturbate` |
| `stop-all` | `python scr33nx_ctl.py stop-all` |
| `monitor recorder|saved on|off` | `python scr33nx_ctl.py monitor recorder on` |
| `pipeline on|off` | `python scr33nx_ctl.py pipeline on` |
| `open` / `close` | `python scr33nx_ctl.py open` |

Behind the scenes it talks to the **Local Control API** (documented in the README):
`/status`, `/add`, `/record`, `/auto`, `/remove`, `/stop_all`, `/monitor`,
`/pipeline`, `/quit`. `open` launches `StreamRecorder.bat` and waits for the API
to come up; `close` calls `/quit` and waits for the app to go down.

> There's also an older single-purpose script, `openclaw_record.py`, that only
> does the record flow. `scr33nx_ctl.py record ...` supersedes it.

---

## 4. The working OpenClaw setup (reference)

This is the configuration that ended up working, so it can be rebuilt if needed.
Config file: `C:\Users\luiis\.openclaw\openclaw.json`.

- **Provider/model:** `anthropic/claude-sonnet-4-6` (Sonnet — lighter on the
  subscription than Opus). Model IDs in OpenClaw **must** include the
  `provider/` prefix, or it silently falls back to `openai/...` and fails.
- **Auth:** the Anthropic provider's **"Claude CLI"** runtime, using a
  subscription **setup-token** (no per-token API billing). This means:
  - The Claude Code CLI must be installed: `npm install -g @anthropic-ai/claude-code`
  - …and logged into your Claude account: run `claude`, sign in.
  - The model entry uses `"agentRuntime": { "id": "claude-cli" }`.
  - `cliBackends["claude-cli"].command` points at the **real exe**, not the npm
    shim:
    `C:\Users\luiis\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`
- **Channel:** Telegram bot, with your user paired as the owner
  (`openclaw pairing approve telegram <code>`).

### Why those exact choices (the gotchas)

These cost real time; they're written down so they don't recur:

1. **`provider/model` is mandatory.** A bare `claude-3-5-sonnet` (or even
   `claude-opus-4-8`) resolves to `openai/...` → *"Unknown model."*
2. **Two billing pools.** The **direct API** path bills against Anthropic
   *"usage credits"* (a separate, possibly-empty pool → *"You're out of extra
   usage"*). The **Claude CLI** runtime bills against your **Pro subscription
   session limits** (which you have). For a free Pro setup you want the **CLI
   runtime**, not the direct-API runtime.
3. **Windows can't spawn the npm `claude` shim.** `npm i -g` creates
   `claude.cmd`/`claude.ps1` (not a real `.exe`), and the OpenClaw gateway runs
   as a **scheduled task with a stripped PATH** → `spawn claude ENOENT`, then
   `EINVAL` when pointed at the `.cmd`. Fix: point `cliBackends` at the real
   `bin\claude.exe` shipped inside the package.
4. **Billing circuit-breaker.** Repeated billing errors trip a provider
   *cooldown* (*"Provider anthropic is in cooldown … (billing)"*) that suspends
   replies even after the real fix. Clear it with `openclaw gateway restart`.

### Handy OpenClaw commands

```
openclaw gateway restart                       # apply config changes / clear cooldown
openclaw config get agents.defaults            # see the current model + runtime
openclaw capability model run --model anthropic/claude-sonnet-4-6 --prompt "OK"  # smoke test
```

---

## 5. First-time activation / after changes

Whenever Scr33nX's API code (`app.py`) changes, or the bot's instructions
(`AGENTS.md`) change:

1. **Restart Scr33nX** (close and reopen the app) so new API endpoints load.
2. **In Telegram, send `/new`** so the bot reloads `AGENTS.md`.
3. Try a command, e.g. *"start the monitor"* or *"stop everything."*

---

## 6. How to teach the bot new commands

The mental model from [§1](#1-the-big-picture): **muscles → hands → brain.**

1. **Muscles — add an API endpoint** in `app.py`. Inside `_ApiHandler.do_POST`
   (or `do_GET`), route a new path to a handler that reads JSON and schedules
   the real work on the UI thread via `app.after(0, …)`. **Never** call a method
   that opens a modal dialog (`messagebox`) from the API — it freezes the app
   that serves the API. Use a no-dialog variant (see the `_api_*` methods).
2. **Hands — add a subcommand** to `scr33nx_ctl.py`: a `cmd_*` function that
   POSTs to your new endpoint, plus an `add_parser` line in `main()`.
3. **Brain — describe it** in `AGENTS.md` under "🔴 Scr33nX — Control": add a row
   to the command-map table (what the user might say → which command).

Then restart Scr33nX and send `/new`. That's the whole loop.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Bot: *"Is the app running?"* | Scr33nX is closed → say *"open Scr33nX"* or launch it. |
| New command does nothing / 404 | Scr33nX wasn't restarted after the `app.py` change. Restart it. |
| Bot ignores a new phrasing | You didn't send `/new` after editing `AGENTS.md`. |
| Bot: *"Unknown model"* | Model id missing the `provider/` prefix in `openclaw.json`. |
| Bot: *"out of extra usage"* | It's on the direct-API runtime (paid credits). Switch the model to the **Claude CLI** runtime (see §4). |
| Bot: *spawn claude ENOENT / EINVAL* | `cliBackends` not pointing at the real `bin\claude.exe`, or `claude` not installed/logged-in. |
| Bot: *"Provider … in cooldown (billing)"* | Stale circuit-breaker → `openclaw gateway restart`. |
| `close` seems to hang | It flushes active recordings first (can take ~20 s with many). That's normal. |

---

## 8. Files involved

| File | Role |
|---|---|
| `app.py` | Scr33nX + its local API (`_ApiHandler`, the `_api_*` methods) |
| `scr33nx_ctl.py` | The control script the bot runs |
| `openclaw_record.py` | Older record-only script (superseded by `scr33nx_ctl.py record`) |
| `StreamRecorder.bat` | GUI launcher (used by `open`) |
| `C:\Users\luiis\.openclaw\workspace\AGENTS.md` | The bot's instructions / command map |
| `C:\Users\luiis\.openclaw\openclaw.json` | OpenClaw config (model, auth, channel) |

> Security note: the local API is loopback-only and unauthenticated — anything
> on your PC can call it. Don't expose port 5200 to the network. The bot's reach
> is limited to whatever `AGENTS.md` tells it and whatever the API exposes.
