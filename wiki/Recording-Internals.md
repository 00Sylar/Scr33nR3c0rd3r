# Recording Internals

> **Full technical reference:** `docs/RecordingLogics.md` in the repository is the
> complete, per‑site "how to make it work" documentation — resolver protocols,
> relay internals, the Stripchat MOUFLON/Playwright paths, the MyFreeCams FCS
> websocket, exit codes, and a troubleshooting map. This page is the overview.

## The local relay (`cb_relay.py`)

ffmpeg never talks to the CDN directly. All HLS traffic goes through a relay on
`127.0.0.1` which:

- **pins the highest‑bitrate variant within your quality cap** (per‑model
  override → global setting → unlimited) so quality can't silently change
  mid‑recording;
- **prefetches upcoming segments in parallel** (64 shared workers, in‑memory
  cache) so ffmpeg is served instantly and never falls behind the live window —
  the cause of 1–2 s timestamp jumps;
- **survives the CDN's mid‑segment TLS resets** that corrupt direct ffmpeg
  downloads;
- **detects segments that expired** before they could be downloaded and reports
  them (Activity Log + optional notification);
- **feeds the bandwidth meter** by counting every byte fetched upstream.

## Per‑site paths

- **Chaturbate / Camsoda** — public HLS resolver → relay → `ffmpeg -c copy`.
- **MyFreeCams** — no public API. A guest login over MFC's FCS websocket
  (`mfc.py`) resolves the model's video state + HLS edge, then relay →
  `ffmpeg -c copy`.
- **Stripchat** — native MOUFLON‑decrypting path through the relay (no browser)
  when possible; otherwise a Playwright‑driven Chromium session
  (`stripchat_live.py`) writes the MPEG‑TS directly.
  *Note: the browser fallback doesn't pass through the relay, so it isn't
  counted by the bandwidth meter.*

## Quality, downgrade & splitting

- **Quality cap** resolution order: per‑model override → global *Max Quality* →
  unlimited.
- **Auto‑Downgrade** (optional, not for Stripchat) restarts only a struggling
  stream one step lower (720p → 480p → 240p) after it loses ≥10 s of segments
  within 60 s; it resets when that model's session ends and never overrides a
  manual per‑model quality choice.
- **File splitting** stops ffmpeg gracefully so the `.ts` trailer flushes,
  re‑fetches the URL, and continues into the next `_partNNN`.

See [[Settings]] for the user‑facing toggles and [[Troubleshooting]] for symptoms
and fixes.
