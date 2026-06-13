#!/usr/bin/env python3
"""
scr33nx_ctl.py — one control surface for OpenClaw (or any agent) to drive Scr33nX.

Talks to the running app's local API on 127.0.0.1:5200. Scr33nX must be OPEN —
this script only sends commands to it, it does not launch it. Every call prints
a single line of JSON so an agent can parse the result and report back.

COMMANDS
  status        <link|name> [--site S]        show a model's state
  record        <link|name> [--site S] [--auto-only] [--no-auto]
                                              add to recorder, AUTO on, start now
  stop          <link|name> [--site S]        stop recording one model
  add-recorder  <link|name> [--site S]        add to Recorder (no AUTO/record)
  add-saved     <link|name> [--site S]        add to Saved Models
  remove        <link|name> [--site S]        remove from Recorder
  remove-saved  <link|name> [--site S]        remove from Saved Models
  auto          <link|name> on|off [--site S] turn AUTO on/off for a model
  stop-all                                    stop ALL downloads + clear AUTO
  monitor       recorder|saved on|off         start/stop a monitor/scanner
  pipeline      on|off                         start/stop the Telegram pipeline
  open                                          launch the Scr33nX app
  close                                         gracefully quit the Scr33nX app

EXAMPLES
  python scr33nx_ctl.py record "https://chaturbate.com/name/"
  python scr33nx_ctl.py stop name --site stripchat
  python scr33nx_ctl.py stop-all
  python scr33nx_ctl.py monitor recorder on
  python scr33nx_ctl.py pipeline on
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

API = "http://127.0.0.1:5200"
SITES = ("chaturbate", "stripchat", "camsoda", "myfreecams")


def parse_model_input(raw: str, default_site: str = "") -> tuple[str, str]:
    """Turn a URL/username into (name, site). Mirrors the app's parser."""
    raw = raw.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
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
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"cannot reach Scr33nX API: {e.reason}. "
                                      f"Is the app running?"}


def _resolve(raw: str, site_override: str) -> tuple[str, str, dict | None]:
    """Return (name, site, error_dict). error_dict is None on success."""
    name, site = parse_model_input(raw, site_override)
    if not name:
        return "", "", {"ok": False,
                        "error": "could not extract a username from the input"}
    if site not in SITES:
        return "", "", {"ok": False,
                        "error": f"unknown site '{site or '(none)'}'. Pass a full "
                                 f"link or use --site ({', '.join(SITES)})."}
    return name, site, None


def cmd_status(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("GET", f"/status?name={name}&site={site}")


def cmd_record(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    steps = []
    add = _request("POST", "/add", {"name": name, "site": site,
                                    "target": "recorder"})
    steps.append({"add": add})
    already = (not add.get("ok")) and "already" in str(add.get("error", "")).lower()
    if not add.get("ok") and not already:
        return {"ok": False, "name": name, "site": site,
                "error": add.get("error", "add failed"), "steps": steps}
    # wait until it's in the recorder list (the add runs on the app UI thread)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if _request("GET", f"/status?name={name}&site={site}").get("in_recorder"):
            break
        time.sleep(0.3)
    if not a.no_auto:
        steps.append({"auto": _request("POST", "/auto",
                                       {"name": name, "site": site, "enabled": True})})
    if not a.auto_only:
        st = _request("GET", f"/status?name={name}&site={site}")
        if st.get("status") == "recording":
            steps.append({"record": {"ok": True, "note": "already recording"}})
        else:
            steps.append({"record": _request("POST", "/record",
                          {"name": name, "site": site, "action": "start"})})
    return {"ok": True, "name": name, "site": site, "steps": steps,
            "final_status": _request("GET", f"/status?name={name}&site={site}")}


def cmd_stop(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/record",
                    {"name": name, "site": site, "action": "stop"})


def cmd_add_recorder(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/add",
                    {"name": name, "site": site, "target": "recorder"})


def cmd_add_saved(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/add",
                    {"name": name, "site": site, "target": "saved"})


def cmd_remove(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/remove",
                    {"name": name, "site": site, "target": "recorder"})


def cmd_remove_saved(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/remove",
                    {"name": name, "site": site, "target": "saved"})


def cmd_auto(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/auto",
                    {"name": name, "site": site, "enabled": a.state == "on"})


def cmd_stop_all(a):
    return _request("POST", "/stop_all")


def cmd_monitor(a):
    return _request("POST", "/monitor",
                    {"target": a.which, "enabled": a.state == "on"})


def cmd_pipeline(a):
    return _request("POST", "/pipeline", {"enabled": a.state == "on"})


def _api_up() -> bool:
    """True if Scr33nX's API answers (i.e. the app is running)."""
    s = _request("GET", "/status?name=__probe__&site=chaturbate")
    return s.get("in_recorder") is not None


def cmd_open(a):
    if _api_up():
        return {"ok": True, "note": "Scr33nX is already running"}
    bat = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "StreamRecorder.bat")
    if not os.path.exists(bat):
        return {"ok": False, "error": f"launcher not found: {bat}"}
    try:
        os.startfile(bat)  # launches the GUI detached, like a double-click
    except Exception as e:
        return {"ok": False, "error": f"failed to launch: {e}"}
    deadline = time.time() + 25
    while time.time() < deadline:
        if _api_up():
            return {"ok": True, "note": "Scr33nX started"}
        time.sleep(1.0)
    return {"ok": False,
            "error": "launched, but the app's API did not come up within 25s"}


def cmd_close(a):
    if not _api_up():
        return {"ok": True, "note": "Scr33nX is not running"}
    r = _request("POST", "/quit")
    deadline = time.time() + 12
    while time.time() < deadline:
        if not _api_up():
            return {"ok": True, "note": "Scr33nX closed"}
        time.sleep(0.5)
    # quit was accepted but shutdown (flushing recordings) may still be running
    return r if r.get("ok") else {"ok": True, "note": "quit signal sent"}


def main() -> int:
    p = argparse.ArgumentParser(description="Control Scr33nX from the command line.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_target(sp, with_site=True):
        sp.add_argument("target", help="model URL or username")
        if with_site:
            sp.add_argument("--site", default="", choices=("",) + SITES,
                            help="site for a bare username")

    s = sub.add_parser("status");        add_target(s); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("record");        add_target(s)
    s.add_argument("--auto-only", action="store_true")
    s.add_argument("--no-auto",  action="store_true")
    s.set_defaults(fn=cmd_record)
    s = sub.add_parser("stop");          add_target(s); s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("add-recorder");  add_target(s); s.set_defaults(fn=cmd_add_recorder)
    s = sub.add_parser("add-saved");     add_target(s); s.set_defaults(fn=cmd_add_saved)
    s = sub.add_parser("remove");        add_target(s); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("remove-saved");  add_target(s); s.set_defaults(fn=cmd_remove_saved)
    s = sub.add_parser("auto");          add_target(s)
    s.add_argument("state", choices=("on", "off"))
    s.set_defaults(fn=cmd_auto)
    s = sub.add_parser("stop-all");      s.set_defaults(fn=cmd_stop_all)
    s = sub.add_parser("monitor")
    s.add_argument("which", choices=("recorder", "saved"))
    s.add_argument("state", choices=("on", "off"))
    s.set_defaults(fn=cmd_monitor)
    s = sub.add_parser("pipeline")
    s.add_argument("state", choices=("on", "off"))
    s.set_defaults(fn=cmd_pipeline)
    s = sub.add_parser("open");  s.set_defaults(fn=cmd_open)
    s = sub.add_parser("close"); s.set_defaults(fn=cmd_close)

    args = p.parse_args()
    res = args.fn(args)
    print(json.dumps(res))
    return 0 if res.get("ok", False) or res.get("in_recorder") is not None else 1


if __name__ == "__main__":
    sys.exit(main())
