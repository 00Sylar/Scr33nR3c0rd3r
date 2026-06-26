# Browser Extension

The extension adds a model to Scr33nX with **one click** while you're on their
page, and lets you set a **1–5 star rank** from the popup once the model is in
Saved Models or the Recorder. Scr33nX must be running — the extension talks to
the app's local API at `http://localhost:5200`.

> The Chromium and Firefox popups are functionally identical; they differ only
> in `chrome.` vs `browser.` API calls.

## Install — Chromium (Chrome / Brave / Opera / Edge)

1. Open `chrome://extensions` (or `brave://`, `opera://`, `edge://extensions`).
2. Enable **Developer mode** (toggle in the corner).
3. Click **Load unpacked** and select the `extension/Chromium` folder.

## Install — Firefox

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add‑on…**.
3. Select `extension/Firefox/manifest.json`.

> Firefox removes temporary add‑ons on restart — reload it after restarting, or
> package it as a signed add‑on for a permanent install.

## What the popup does

- **Add** the current model's page to the Recorder or Saved Models in one click
  (the site is auto‑detected from the URL).
- **Rank** the model 1–5 stars — enabled only once the model is in Saved Models
  or the Recorder (otherwise the stars show disabled with a hint, to avoid
  "orphan" ranks).
- **Live status** — while the popup is open it keeps polling the backend, so the
  model's status, rank, and list membership update in place.

Under the hood the popup calls the [[Local Control API|Local-Control-API]]
(`/status`, `/add`, `/rank`, …). If clicks do nothing, confirm Scr33nX is
running and only **one** instance is open (see [[Troubleshooting]]).
