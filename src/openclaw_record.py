#!/usr/bin/env python3
"""
openclaw_record.py — one-shot bridge for OpenClaw (or any agent) to drive Scr33nX.

Give it a model link (or plain username) and it will, against the running app's
local API on 127.0.0.1:5200:

    1. add the model to the Recorder
    2. wait until it actually shows up in the Recorder list
    3. toggle AUTO on
    4. start recording now (skipped automatically if she isn't live and you
       pass --auto-only)

Scr33nX must already be running — this script only talks to it, it does not
launch it. Run it on the SAME machine as the app (the API is localhost-only).

USAGE
    python openclaw_record.py "https://chaturbate.com/somemodel/"
    python openclaw_record.py "myfreecams.com/#somemodel"
    python openclaw_record.py somemodel --site stripchat
    python openclaw_record.py "<link>" --auto-only      # add + AUTO, don't force-start
    python openclaw_record.py "<link>" --no-auto        # add + start, leave AUTO off

It prints a single JSON line summarising what happened, so an agent can parse
the result and report back over WhatsApp/Telegram.
"""

import sys
import json
import time
import argparse
import urllib.request
import urllib.error

API = "http://127.0.0.1:5200"
SITES = ("chaturbate", "stripchat", "camsoda", "myfreecams")


def parse_model_input(raw: str, default_site: str = "") -> tuple[str, str]:
    """Mirror of the app's _parse_model_input: turn a URL/username into
    (name, site). Returns ("", "") if no username could be extracted."""
    raw = raw.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]

    # MyFreeCams: model name lives in the URL hash fragment (.../#name)
    if raw.startswith("myfreecams.com"):
        frag = raw.split("#", 1)[1] if "#" in raw else ""
        frag = frag.lstrip("/")
        if frag.startswith("model/"):
            frag = frag[len("model/"):]
        username = frag.split("/")[0].split("?")[0]
        return username.strip("/"), "myfreecams"

    for domain, site in (("chaturbate.com", "chaturbate"),
                         ("stripchat.com",  "stripchat"),
                         ("camsoda.com",    "camsoda")):
        if raw.startswith(domain):
            parts = raw.split("/")
            username = parts[1] if len(parts) > 1 else ""
            return username.strip("/"), site

    # Plain username — needs an explicit site
    return raw.strip("/"), default_site


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        # The API returns JSON error bodies even on 4xx — surface them.
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"cannot reach Scr33nX API: {e.reason}. "
                                      f"Is the app running?"}


def get_status(name: str, site: str) -> dict:
    return _request("GET", f"/status?name={name}&site={site}")


def wait_in_recorder(name: str, site: str, timeout: float = 8.0) -> bool:
    """Poll /status until the model appears in the Recorder list (the /add is
    handled on the app's UI thread, so it isn't instant)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = get_status(name, site)
        if st.get("in_recorder"):
            return True
        time.sleep(0.3)
    return False


def run(raw: str, site_override: str = "", do_auto: bool = True,
        do_record: bool = True, auto_only: bool = False) -> dict:
    name, site = parse_model_input(raw, site_override)
    steps: list[dict] = []
    result = {"input": raw, "name": name, "site": site, "steps": steps,
              "ok": False}

    if not name:
        result["error"] = "could not extract a username from the input"
        return result
    if site not in SITES:
        result["error"] = (f"unknown site '{site or '(none)'}'. Pass a full link "
                            f"or use --site ({', '.join(SITES)}).")
        return result

    # 1. add to recorder
    add = _request("POST", "/add", {"name": name, "site": site,
                                    "target": "recorder"})
    steps.append({"add": add})
    already = (not add.get("ok")) and "already" in str(add.get("error", "")).lower()
    if not add.get("ok") and not already:
        result["error"] = add.get("error", "add failed")
        return result

    # 2. wait until it's really in the list
    if not wait_in_recorder(name, site):
        result["error"] = "added but never appeared in the Recorder list (timeout)"
        return result

    # 3. AUTO on
    if do_auto:
        auto = _request("POST", "/auto", {"name": name, "site": site,
                                          "enabled": True})
        steps.append({"auto": auto})

    # 4. start recording now (unless the caller only wants AUTO armed)
    if do_record and not auto_only:
        st = get_status(name, site)
        if st.get("status") == "recording":
            steps.append({"record": {"ok": True, "note": "already recording"}})
        else:
            rec = _request("POST", "/record", {"name": name, "site": site,
                                               "action": "start"})
            steps.append({"record": rec})

    result["ok"] = True
    result["final_status"] = get_status(name, site)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="Add a model to Scr33nX, arm AUTO, "
                                            "and start recording.")
    p.add_argument("link", help="model URL or plain username")
    p.add_argument("--site", default="", choices=("",) + SITES,
                   help="site for a plain username (ignored when a URL is given)")
    p.add_argument("--no-auto", action="store_true", help="do not toggle AUTO")
    p.add_argument("--auto-only", action="store_true",
                   help="add + arm AUTO but do not force-start recording now")
    args = p.parse_args()

    res = run(args.link, site_override=args.site,
              do_auto=not args.no_auto, auto_only=args.auto_only)
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
