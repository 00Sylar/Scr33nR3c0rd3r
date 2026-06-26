# Telegram Upload Pipeline (optional)

The **Output / Upload** tab can convert finished `.ts` recordings to `.mp4` and
upload them to a Telegram group/topic automatically.

## Setup

1. Get your `api_id` / `api_hash` from <https://my.telegram.org>.
2. Fill in the **Telegram / Pipeline settings** in the Output / Upload tab
   (group ID, optional topic ID).
3. Save and enable the pipeline.

Settings are stored in `Pipeline/pipeline_settings.json`. The pipeline requires
the `tdjson` package (installed via `requirements.txt`).

## How it behaves

- Finished `.ts` recordings are converted to `.mp4` and uploaded to the
  configured group/topic.
- Large `.mp4` files are split using the same 3‑digit `_partNNN` padding as the
  recorder's own file splitting.
- **Stopping the pipeline** lets the in‑flight upload finish, then halts the
  uploader workers and closes the TDLib clients until you resume — it no longer
  keeps draining the queued backlog after you stop the converter.

## Controlling it remotely

The pipeline can be toggled through the [[Local Control API|Local-Control-API]]
(`POST /pipeline {enabled}`) and therefore from the
[[OpenClaw Bot|OpenClaw-Bot]] ("start/stop the pipeline").
