# Settings

All settings live in the left panel and are saved automatically when you click
**💾 Save Settings**.

| Setting | What it does |
|---|---|
| **Output Folder** | Where recordings are saved. |
| **Max File Size (MB)** | Split recordings into parts at this size (empty = unlimited). |
| **Check Interval (sec)** | How often each model's status is polled (default 30). |
| **Max Quality (all models)** | Global stream‑quality cap: Unlimited / 1080p / 720p / 480p. Applied when a recording (re)starts. Right‑click a model for a per‑model override that beats this. |
| **Minimize to SysTray** | Hide to the tray instead of the taskbar. |
| **Notifications** | Master toggle for Windows toast notifications. |
| **⚠ Dropped‑Segment Warnings** | Toast when a recording is losing segments due to saturated bandwidth (always logged to the Activity Log regardless). |
| **⬇ Auto‑Downgrade Quality** | Restart a stream one quality step lower when it keeps losing segments (≥10 s lost within 60 s). Only that stream is touched; manual per‑model quality choices are respected. Not available for Stripchat. |
| **🎭 Stripchat Browser Fallback** | When Stripchat's browserless native path can't resolve a stream, fall back to the Playwright browser recorder. Enabled by default. Uncheck to record native‑only — if native fails the stream is skipped and the browser never launches. |
| **🔒 Privacy Mode** | Idle screen cover. Clicking the cover shows an Exit / Stay panel drawn on the cover (no modal). |

## Per‑model quality override

Right‑click any model in the Recorder to set a quality cap just for that model.
A per‑model override **always beats** the global *Max Quality* and is never
touched by Auto‑Downgrade.

## Where settings are stored

- App settings, models, and ranks: `~/.streamrecorder_config.json`
  (ranks live under the `ranks` key).
- Telegram pipeline settings: `Pipeline/pipeline_settings.json`
  (see [[Telegram Pipeline|Telegram-Pipeline]]).
- Logs: `%LOCALAPPDATA%\Scr33nX\streamrecorder.log` (rotating, 5 MB × 3).
