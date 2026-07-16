# Usage

*These steps apply to both interfaces — the default UI and the classic one
(`--classic`) behave the same way; only the look differs. See [[Installation]].*

1. **Add models** using the left panel — enter the username (or paste a full
   model URL; the site is auto‑detected) and select the site: `chaturbate`,
   `stripchat`, `camsoda`, or `myfreecams`.
2. Click **▶ START MONITOR** — the app polls each model at the configured
   interval.
3. When a model goes live, recording starts automatically and you get a
   notification.
4. When the model goes offline, the recording stops automatically.
5. If you set a **Max File Size**, the recording splits into numbered parts
   (`_part001`, `_part002`, …). A recording that never reaches the limit stays a
   single, unsuffixed file.
6. Watch the **↓ Mbps** meter in the header while recording several models — if
   streams start dropping segments you'll also get a warning notification.
7. **Rate models** — give any model 1–5 stars on the Recorder or Saved Models
   tab (click a star in the **RANK** column, or right‑click → **Set Rank** to
   rate a whole selection), then click the `RANK` header to sort. Ranks stay
   with the model across both tabs and persist between sessions.
8. **Preview a stream** — right‑click an **online (or recording)** model →
   **Preview** to watch it live (offline models are skipped with a note).
   Choose external or embedded, and the preferred engine, under **Settings →
   Stream preview**. The embedded preview has **▶ REC / ⏹ Stop** buttons next
   to a live status badge — REC starts recording the model on the spot (adding
   it to the Recorder first if needed).
9. **Watch several at once** *(default UI)* — the **▶ Player** tab opens
   models as live muted tiles in a **Grid** wall; click one for **Theater**
   mode (large player with controls and its own REC/Stop, the rest in a
   thumbnail strip). Add tiles with **+ Add Tile** or right‑click an online
   model → **▶ Add to Player**. Tiles keep streaming while you're on other
   tabs (no reload when you come back); **🧹 Clear Player** empties the tab
   in one click. Tile count is capped by **Max Player tiles** in Settings,
   since every open tile is a live stream.

---

## Output files

Saved to the configured output folder (default `~/Videos/StreamRecorder`).

```
modelname_CB_20240515_143022.ts            ← single file (no split)
modelname_ST_20240515_143022.ts
modelname_CS_20240515_143022_part001.ts    ← split into parts
modelname_CS_20240515_143022_part002.ts
```

- A recording that never hits **Max File Size** is one unsuffixed file. When it
  splits, every part shares the same timestamp and is numbered `_part001`,
  `_part002`, … (3‑digit). The Telegram pipeline uses the same scheme for `.mp4`
  splits.
- Site codes: `CB` = Chaturbate, `ST` = Stripchat, `CS` = Camsoda,
  `MFC` = MyFreeCams.
- `.ts` container — plays in VLC, MPV, or any MPEG‑TS player.
- Convert to `.mp4`: `ffmpeg -i input.ts -c copy output.mp4`

## File splitting

Set **Max File Size (MB)** in Settings. When the current file reaches that size
the recorder stops the current ffmpeg/Playwright process gracefully (so the
`.ts` trailer flushes), re‑fetches the stream URL, starts a new part, and sends
a notification — without missing a moment of the stream.

## Bandwidth & quality tips

- Each 1080p stream needs roughly **5–6 Mbps** sustained.
- If many recordings drop segments (watch for ⚠ warnings), your total internet
  bandwidth is the limit. In order of preference: set a global **Max Quality**
  cap (720p roughly halves usage vs. unlimited), enable **⬇ Auto‑Downgrade** so
  only struggling streams lose quality, or record fewer models at once.

See [[Settings]] for every option, and [[Troubleshooting]] if something misbehaves.
