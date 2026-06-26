# Local Control API (port 5200)

While Scr33nX is running it serves a small HTTP API on
`http://127.0.0.1:5200`, used by the [[Browser Extension|Browser-Extension]]
**and** by external automation (e.g. the [[OpenClaw Bot|OpenClaw-Bot]]).

> **Security:** the API is **loopback‑only** (no remote access) and
> **unauthenticated** — anything running on your PC can call it. Don't expose
> port 5200 to the network.

## Endpoints

| Method & path | Body | Action |
|---|---|---|
| `GET /status` | `?name=&site=` | model state: `in_recorder`, `in_saved`, `status`, `auto`, `rank` |
| `GET /dashboard` | — | aggregate snapshot: per‑site (`CB`/`SC`/`CS`/`MFC`) + `all` totals of `total`/`recording`/`online`/`offline` |
| `POST /add` | `{name, site, target}` | add to `recorder` or `saved` |
| `POST /record` | `{name, site, action}` | `start` / `stop` recording one model |
| `POST /auto` | `{name, site, enabled}` | toggle AUTO for a model |
| `POST /rank` | `{name, site, rank}` | set a model's 0–5 star rank (`0` clears); the model must already be in Saved Models or the Recorder |
| `POST /remove` | `{name, site, target}` | remove from `recorder` or `saved` |
| `POST /stop_all` | — | stop every active download + clear all AUTO |
| `POST /clear` | — | stop monitor + all downloads, clear AUTO, remove every Recorder model (Saved kept) |
| `POST /monitor` | `{target, enabled}` | start/stop the `recorder` monitor or `saved` scanner |
| `POST /pipeline` | `{enabled}` | start/stop the Telegram upload pipeline |
| `POST /quit` | — | gracefully shut the app down |

All POST actions run through no‑dialog code paths so they never block the UI
thread that serves the API.

## Command‑line helper

`scr33nx_ctl.py` wraps every endpoint (plus `open`, which launches the app):

```
python scr33nx_ctl.py <command> [args]
python scr33nx_ctl.py --help
```

See [[OpenClaw Bot|OpenClaw-Bot]] for the full command list and how a chat bot
drives these from your phone.
