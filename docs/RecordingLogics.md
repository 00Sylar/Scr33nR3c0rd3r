# Scr33nX — Recording Logics

**"HOW TO MAKE IT WORK" — full technical reference for how Scr33nX records live
streams from each supported site.**

This document is the single source of truth for the recording pipeline. It is
written so that another engineer — or another AI/LLM — can read it cold and
understand exactly how each site is resolved, recorded, and kept healthy,
without having to reverse-engineer the code first. When a site breaks, this is
where you start.

---

## 0. The big picture

Every recording follows the same skeleton, regardless of site:

```
  status check ──► resolve stream URL ──► (relay) ──► ffmpeg -c copy ──► .ts file
       │                  │                  │              │
   "is she live          per-site         local HTTP     graceful
    & public?"          resolver          proxy on        shutdown
                                          127.0.0.1       flushes trailer
```

Four sites are supported, each with its own **resolver** but a shared
**recording core**:

| Site | Tag | Resolver file | Recording path |
|---|---|---|---|
| Chaturbate | `CB` | `recorder.get_chaturbate_stream_url` | relay → `ffmpeg -c copy` |
| Stripchat | `ST` | `recorder.get_stripchat_stream_url` + `stripchat_native` | relay → ffmpeg (native), else Playwright browser |
| Camsoda | `CS` | `recorder.get_camsoda_stream_url` | relay → `ffmpeg -c copy` |
| MyFreeCams | `MFC` | `mfc.get_stream_url` | relay → `ffmpeg -c copy` |

The orchestration logic (monitoring, auto-start/stop, file splitting, stall
detection, restarts) lives in `recorder.py` in the `StreamRecorder` class and is
**identical for all four sites**. Only URL resolution and (for Stripchat) the
recording mechanism differ per site.

**Key files:**
- `recorder.py` — resolvers + `StreamRecorder` orchestration + ffmpeg launch
- `cb_relay.py` — local HTTP relay (quality pinning, prefetch, bandwidth meter)
- `stripchat_native.py` — browserless Stripchat (MOUFLON decryption)
- `stripchat_live.py` — Playwright/Chromium fallback for Stripchat
- `mfc.py` — MyFreeCams FCS websocket resolver
- `app.py` — GUI + extension HTTP API (port 5200)

---

## 1. The local relay (`cb_relay.py`) — the heart of recording

ffmpeg **never talks to the CDN directly** for Chaturbate, Camsoda, and
MyFreeCams (and for the native Stripchat path). Instead it is pointed at a small
HTTP server running on `127.0.0.1` (plain HTTP, random port), and that relay
fetches upstream using Python's `requests`. This solves four problems at once:

1. **TLS resets** — Chaturbate's LL-HLS CDN edges reset ffmpeg's TLS connection
   mid-segment (Schannel error -10054, "session has been invalidated"), which
   truncates fMP4 segments and corrupts the `.ts`. `requests` downloads the same
   bytes reliably.
2. **Quality pinning & caps** — the relay rewrites the master playlist to keep
   **only one variant** (`_select_highest_variant`), so ffmpeg physically
   cannot fall back to a lower resolution mid-recording. By default that's the
   highest-BANDWIDTH variant; if a quality cap applies (see §1b), it's the
   highest-bandwidth variant whose `RESOLUTION` height is ≤ the cap (lowest
   available as fallback if every variant is above the cap).
3. **Parallel prefetch** — ffmpeg's HLS demuxer fetches segments one at a time;
   if a download is slower than the segment duration it falls permanently behind
   the live window and segments expire (causing 1–2 s timestamp jumps). The
   relay already knows upcoming segment URLs (it rewrote the playlist), so it
   prefetches them with **64 worker threads** into an in-memory cache and serves
   ffmpeg instantly.
4. **Bandwidth metering + drop detection** — every byte fetched upstream is
   counted (`bytes_downloaded()` drives the `↓ Mbps` meter). Segments that
   expire from the playlist before they could be fetched are reported via a gap
   callback (Activity Log + optional toast).

### Concurrency sizing (learned the hard way)

The prefetch pool is **shared by all streams**: with N concurrent recordings
and ~2 s segments it must complete ~N/2 downloads per second. The original
16 workers — each stallable for up to 60 s (3 × 20 s retries) — starved at
~10–15 streams, making *every* stream drop segments at once. Current sizing,
validated at ~50 concurrent recordings:

| Knob | Value | Why |
|---|---|---|
| `_PREFETCH_WORKERS` | 64 | ~N/2 downloads/s for N≈50 streams with headroom |
| prefetch fetch budget | 2 tries, 5 s connect / 10 s read | a live segment only exists ~10–20 s; fail fast, next playlist refresh retries |
| `_CACHE_MAX_BYTES` | 768 MB | 300 MB silently disabled prefetching at high N; a warning now logs if the cap is hit |
| HTTP pool | 32 hosts / 128 connections | workers must never block on a free connection |
| `request_queue_size` | 128 | listen-backlog default of 5 refused bursts of ffmpeg connections (ffmpeg "Error number -138") |

### How wrapping works

```python
relay_url = cb_relay.wrap(upstream_master_url, USER_AGENT, mode=site, label="site:model")
# returns e.g. http://127.0.0.1:54321/p.m3u8?m=chaturbate&l=...&u=<encoded upstream url>
```

- `mode` selects the playlist transform: `chaturbate` / `camsoda` / `myfreecams`
  (pin highest variant, strip LL-HLS tags) or `stripchat` (MOUFLON decrypt).
- `label` (e.g. `chaturbate:alice`) names the stream for gap reporting.
- The relay rewrites **every** URI in every playlist (segments, `EXT-X-MAP`,
  `EXT-X-MEDIA`, `EXT-X-PART`) to also route through the relay.

### Extension normalization gotcha

ffmpeg whitelists segment URLs by **path extension**. The relay normalizes every
non-`.m3u8` URL to end in `.m4s` (a universally whitelisted fragmented-MP4
extension) so odd upstream extensions (e.g. Camsoda's `.fmp4`) aren't rejected.
ffmpeg is also launched with `-allowed_extensions ALL` for relayed sites as a
belt-and-suspenders measure.

### Referer requirement

doppiocdn (Stripchat) and the other CDNs reject segment requests without a
matching `Referer`/`Origin`. The relay injects the correct ones per `mode`
(see `_REFERERS` in `cb_relay.py`).

---

## 1b. Quality caps & auto-downgrade

The relay calls `cb_relay.set_quality_callback(fn)` — registered by
`StreamRecorder` as `effective_quality(label)` — each time it fetches a
**master** playlist (i.e. on recording start/restart, never mid-stream).
The returned int is the max variant height in pixels (0 = unlimited).

**Resolution order** (`recorder.effective_quality`):

```
session auto-downgrade  >  per-model override  >  global setting  >  unlimited
   (recorder._session_q)   (app right-click menu)  (Settings dropdown)
```

- The per-model overrides live in the app (`_model_q`, persisted per model as
  `max_q` in the config JSON) and are shared with the recorder by reference.
- Caps only apply to `chaturbate` / `camsoda` / `myfreecams` modes —
  **Stripchat bypasses `_select_highest_variant`** (its native path resolves a
  keyed variant directly; the Playwright path bypasses the relay entirely).

**Auto-downgrade** (`recorder._maybe_downgrade`, opt-in via the Settings
checkbox → `auto_downgrade_enabled`): driven by the relay's gap callback. If a
stream loses ≥ `10 s` of video within a `60 s` window, the recorder:

1. picks the next rung below the current effective cap from `(720, 480, 240)`,
2. records it in `_session_q[label]` (session-only),
3. gracefully stops ffmpeg — the normal exit handler restarts it, the relay
   re-queries `effective_quality()`, and the lower variant gets pinned.

Guards: 2-minute cooldown per stream after each step (the restart itself is
briefly unstable); models with a per-model override are **never** touched
(explicit user choice); stripchat labels are skipped; at the bottom rung it
just logs. `_session_q` is cleared when the recording ends naturally (offline,
private, manual stop) so the next session starts back at configured quality.
A downgrade restart resets `restart_count` so it doesn't consume the
3-attempt crash-restart budget.

---

## 2. The recording core (`recorder.py`)

### ffmpeg invocation (`launch_ffmpeg_hls`)

For relayed sites the command is essentially:

```
ffmpeg -hide_banner -loglevel error
       -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10
       -allowed_extensions ALL
       [-m3u8_hold_counters 20]      # chaturbate, myfreecams only
       -i <relay_url>
       -c -copy -copyts
       <output_path>.ts
```

- `-c copy` — **no re-encoding**; the stream is muxed as-is into MPEG-TS. Fast,
  lossless, low CPU.
- `-copyts` — preserve timestamps.
- The process is spawned with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` and
  a `stdin` pipe so we can send `q\n` for a **graceful shutdown** (`graceful_stop`),
  which flushes the MPEG-TS trailer and prevents corrupt files. Fallback is
  `terminate()` then `kill()`.

### Output filename format

```
<model>_<TAG>_<YYYYMMDD>_<HHMMSS>[_partNNN].ts
# e.g. alice_CB_20240515_143022_part001.ts
```

`_partNNN` only appears when a Max File Size is configured. Tags: `CB`, `ST`,
`CS`, `MFC` (see `SITE_TAGS`).

### Orchestration (`StreamRecorder`)

- **Monitoring groups** — two independent monitor threads: `recorder` (the
  active record list) and `saved` (the view-only watchlist). A model can be in
  both.
  - The `recorder` loop checks each model sequentially every `check_interval`
    seconds (default 30), with a 5 s base tick for session housekeeping.
  - The `saved` loop uses a **bulk scanner** because per-model polling doesn't
    scale to hundreds of watchlist entries (see §3 Chaturbate).
- **Auto start/stop** — when a resolver returns a URL the model is `ONLINE`;
  recording begins automatically. When she goes offline ffmpeg exits and status
  returns to `OFFLINE`.
- **File splitting** (`_check_split`) — when the current file reaches Max File
  Size: graceful-stop ffmpeg (flush trailer), re-resolve the URL, bump the part
  number, start a new file.
- **Stall detection** (`_check_stall`) — if the output file hasn't grown for
  60 s, ffmpeg is gracefully killed; the exit handler then restarts it.
- **Offline probe on stall** (`_probe_offline_while_stalled`) — because ffmpeg
  often keeps *reconnecting* (rather than exiting) when a model goes offline,
  waiting out the full 60 s left the status stuck on `RECORDING`. So after ~20 s
  of no growth the recorder fires a one-shot resolver check **off the monitor
  thread**: if the model is offline it stops ffmpeg immediately (→ `OFFLINE` in
  ≈25 s); if she's still online (buffering) the recording is left alone and the
  60 s hard-stop remains the backstop. Re-armed whenever the file grows again.
- **Restart logic** (`_handle_ffmpeg_exit`) — on ffmpeg exit code 0/1 the
  recording auto-restarts up to **3 times** (3 s delay, URL re-resolved). For
  Stripchat, exit codes 4 (idle/no segments) and 5 (ticket/private/group show)
  mean "online but not public" → status `PRIVATE`, no restart, 5-minute cooldown.
- **Session watcher** — an always-on background loop handles split/stall/exit
  even when the monitor is off (e.g. user clicked REC manually). It idles while
  any monitor is running to avoid double-handling.

---

## 3. Per-site resolver logic

### Chaturbate (`CB`)

**Single model:** `GET https://chaturbate.com/api/chatvideocontext/{name}/`
returns JSON with `hls_source` (the master playlist URL) and `room_status`.

- `room_status` in `{offline, away, private, hidden, ""}` → not recordable.
- Empty body / HTTP 429/403/503 → **Cloudflare rate-limited**; treated as
  "unknown" (returns None, keeps previous status — does NOT mark offline).
- All CB API calls are serialized through a lock with a **1.5 s minimum gap**
  between requests to avoid Cloudflare hammering.
- The monitor path retries once for CDN warmup; the manual-REC path retries 4×.

**Bulk (saved watchlist):** instead of one request per model, do **one full
room-list sweep** via the paginated public API
(`/api/ts/roomlist/room-list/`, 90 rooms/page, ~100 pages, ~2.5 min at 1.5 s
cadence) and test membership. This avoids the rate-limiting that per-model
polling triggers. Returns `None` on failure so statuses are preserved.

**Recording:** master URL → relay (`mode=chaturbate`) → ffmpeg. The relay strips
LL-HLS partial-segment tags so only full segments are recorded.

### Stripchat (`ST`)

Stripchat is the most complex because its playlists are DRM-protected
("MOUFLON"). There are **two recording paths**, tried in order:

**Path A — native browserless (preferred, `stripchat_native.py`):**
1. Resolve numeric stream id via
   `GET /api/front/v2/models/username/{name}/cam` → `cam.streamName`
   (and `cam.isCamAvailable`).
2. Fetch master playlist
   `https://edge-hls.doppiocdn.com/hls/{id}/master/{id}_auto.m3u8`.
3. The master lists accepted key-ids as `#EXT-X-MOUFLON:PSCH:v2:<keyId>`. We
   maintain a **key table** (`MOUFLON_KEYS`, extendable via
   `stripchat_mouflon_keys.json` next to the module). If no listed key-id matches
   our table → keys rotated → return None → fall back to Path B.
4. Pick the highest-BANDWIDTH variant, append `?psch=v2&pkey=<keyId>`.
5. Validate it's a real public stream (not an advert loop: reject if
   `MOUFLON-ADVERT` or `/cpa/` present).
6. The keyed variant URL goes to the relay with `mode=stripchat`. The relay's
   `rewrite_playlist` (in `stripchat_native.py`) **decrypts** each segment URL:
   the `#EXT-X-MOUFLON:URI:` value's 2nd-to-last `_`-token is the real segment
   name reversed + XOR-encrypted with `SHA256(key)`; decrypt it and substitute
   it for the dummy `media.mp4` line that follows. Then plain `ffmpeg -c copy`
   records it.

> **MOUFLON cipher:** `base64-decode → XOR with cyclic SHA256(key)`. Key table
> is public knowledge from `kesamom/stripchat_mouflon` and
> `lossless1024/StreaMonitor`. When Stripchat rotates keys, add the new
> `keyId: key` pair to `stripchat_mouflon_keys.json` — no code change needed.

**Path B — Playwright/Chromium fallback (`stripchat_live.py`):**
Used when the native path fails (keys rotated, not public, advert loop). A
headless Chromium plays the page (decoding MOUFLON internally); we intercept
segment HTTP responses, reorder them by sequence number (LL-HLS fetches parts in
parallel), and pipe ordered fMP4 bytes into a child ffmpeg that transmuxes to
MPEG-TS. It behaves like a Popen ffmpeg process (live-growing `.ts`, stdin-close
= graceful stop). Exit codes: 4 = idle timeout (no segments in 45 s), 5 =
ticket/private/group show detected.

> **Note:** the Playwright path does NOT go through the relay, so its traffic is
> **not counted** by the bandwidth meter, and it records a single quality (no
> highest-variant pinning beyond what the player chooses).

**Lightweight online check (saved scanner):** fetch the model page and test for
`"isLive": true` — cheaper than the full resolve.

### Camsoda (`CS`)

Simplest resolver. Public endpoint:

```
GET https://www.camsoda.com/api/v1/video/vtoken/{name}
→ { token, edge_servers: ["host/path"], stream_name, status }
```

Build: `https://{edge}/{stream_name}_v1/index.m3u8?token={token}` (the edge
already includes its path segment; `stream_name` embeds the resolution). If
`status` is present and not `"online"`, or any field is missing → not recordable.

**Recording:** URL → relay (`mode=camsoda`) → ffmpeg. Camsoda segments use the
`.fmp4` extension, which is why the relay's extension normalization + ffmpeg's
`-allowed_extensions ALL` matter here.

### MyFreeCams (`MFC`)

MFC has **no public JSON API** mapping a name to a stream. Every working tool
speaks MFC's **FCS chat protocol over a websocket** with a guest login. This is
implemented in `mfc.py`:

1. `GET https://www.myfreecams.com/_js/serverconfig.js` — server maps (cached 1 h).
2. Connect `wss://{xchat}.myfreecams.com/fcsl` (pick an `rfc6455` websocket
   server from the config).
3. Send the handshake frames:
   ```
   hello fcserver\n\0
   1 0 0 20071025 0 {rand}@guest:guest\n     (FCTYPE 1  = LOGIN)
   10 0 0 20 0 {model_name}\n                (FCTYPE 10 = USERNAMELOOKUP)
   ```
4. Server frames are `{6-char length}{FCTYPE} {from} {to} {arg1} {arg2} {payload}`.
   The lookup payload is URI-encoded JSON with `uid`, `vs` (video state), and
   `u.camserv`.

**Video state (`vs`) mapping:**

| vs | meaning | recordable? |
|---|---|---|
| 0 | public chat | ✅ yes → `online` |
| 2 | away | no → `away` |
| 12 / 13 / 14 | private / group / club-curtain | no → `private` |
| 90 / 127 / other | cam off / offline | no → `offline` |

**HLS edge resolution:** `uid_video = uid + 100_000_000`. `camserv` maps to a
video host via serverconfig (`h5video_servers` → prefix `mfc_`, `wzobs_servers`
→ `mfc_a_`, `ngvideo_servers` → `mfc_`; heuristic fallback `video{camserv-500}`).
Candidate playlist URLs are probed and the first answering **200 + `#EXTM3U`**
wins (this hedges the `f4v_mobile` → `f4v_cmaf` CDN migration and serverconfig
gaps):

```
https://{server}.myfreecams.com/NxServer/ngrp:{prefix}{uid_video}.f4v_cmaf/playlist_sfm4s.m3u8
https://{server}.myfreecams.com/NxServer/ngrp:{prefix}{uid_video}.f4v_mobile/playlist.m3u8
```

**Recording:** winning URL → relay (`mode=myfreecams`) → ffmpeg.

**Bulk lookup:** `lookup_models([names])` opens one websocket and does
sequential lookups for the whole watchlist. `last_status(name)` returns the
cached video state so the recorder can show `PRIVATE` without a second round-trip.

> On any protocol drift, every MFC entry point returns None and MFC models
> simply read OFFLINE — the rest of the app is unaffected.

---

## 4. Quick troubleshooting map

| Symptom | Likely cause | Where to look |
|---|---|---|
| All CB models show OFFLINE | Cloudflare rate-limit | `_fetch_chaturbate_once` (429/403/empty) |
| Stripchat won't record, no browser opens | MOUFLON keys rotated; should fall back | `stripchat_native.resolve` returns None → check Playwright |
| Stripchat records but bandwidth meter ignores it | On Playwright fallback (expected — bypasses relay) | `launch_stripchat_playwright` |
| Camsoda "extension not whitelisted" | Relay extension-normalize regressed | `_wrap_url` (.m4s) + `-allowed_extensions ALL` |
| MFC always OFFLINE | serverconfig/protocol drift, or no playlist candidate answered | `mfc._candidate_urls`, `mfc.lookup` |
| `.ts` files corrupt/unplayable | ffmpeg killed instead of graceful 'q' | `graceful_stop` |
| Recording drops segments (⚠ warnings) | Total bandwidth saturated | set a Max Quality cap / enable auto-downgrade; relay gap callback |
| ALL streams drop segments at once | Prefetch pool starved or cache cap hit | §1 concurrency sizing; look for "prefetch cache full" in `streamrecorder.log` |
| ffmpeg "Error number -138" to 127.0.0.1 | Relay listen backlog overflow | `_QuietServer.request_queue_size` |
| Quality silently dropped mid-recording | `_select_highest_variant` not applied | relay `mode` not set, or master not pinned |
| Stream records at lower quality than expected | Quality cap or session auto-downgrade active | §1b; Activity Log "⬇" lines; right-click → Max Quality |
| Upload meter shows impossible speeds | TDLib dedupe/resume reports instant upload | `_bw_tick` spike filter in `app.py` |

---

## 5. Bandwidth budget

Each 1080p stream needs roughly **5–6 Mbps sustained**. If many simultaneous
recordings drop segments (watch for ⚠ warnings), the total internet connection
is the bottleneck. Remedies, in order: set a global **Max Quality** cap
(720p roughly halves usage vs. unlimited), enable **⬇ Auto-Downgrade** so only
the streams that can't keep up lose quality (§1b), or record fewer models at
once. The `↓ Mbps` header meter shows Scr33nX's total upstream download
traffic (relay-routed sites only).

## 6. Beta logging

`streamrecorder.log` (in `%LOCALAPPDATA%\Scr33nX`, rotating 5 MB × 3, UTF-8)
receives everything: Activity Log lines, per-stream ffmpeg stderr, relay
warnings (including "prefetch cache full"), and background-thread tracebacks —
with thread names. `streamrecorder_crash.log` (same folder, reset at startup
once it exceeds 1 MB) captures hard interpreter crashes via `faulthandler`.
When something misbehaves, start there.
