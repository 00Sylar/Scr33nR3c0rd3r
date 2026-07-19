#!/usr/bin/env python3
"""
scr33nx_ctl.py — one control surface for OpenClaw (or any agent) to drive Scr33nX.

Talks to the running app's local API on 127.0.0.1:5200. Scr33nX must be OPEN —
this script only sends commands to it, it does not launch it. Every call prints
a single line of JSON so an agent can parse the result and report back.

COMMANDS
  status        <link|name> [--site S]        show a model's state (incl. rank)
  record        <link|name> [--site S] [--auto-only] [--no-auto] [--rank N]
                                              add to recorder, AUTO on, start now
  stop          <link|name> [--site S]        stop recording one model
  add-recorder  <link|name> [--site S] [--rank N]   add to Recorder (no AUTO/record)
  add-saved     <link|name> [--site S] [--rank N]   add to Saved Models
  rank          <link|name> <0-5> [--site S]  set a model's 1-5 star rank (0 clears)
  remove        <link|name> [--site S]        remove from Recorder
  remove-saved  <link|name> [--site S]        remove from Saved Models
  auto          <link|name> on|off [--site S] turn AUTO on/off for a model
  stop-all                                    stop ALL downloads + clear AUTO
  clear                                        stop everything + REMOVE all models
  dashboard                                    per-site + totals status snapshot
  models        [--site S] [--recording] [--online] [--min-rank N]
                                              list every tracked model with its
                                              status/rank (one bulk call)
  link          <a> <b> [--site-a S] [--site-b S]
                                              mark two models as the same person
                                              (rank syncs; extension warns on
                                              duplicate recording)
  unlink        <link|name> [--site S]        remove a model from its group
  links                                        list identity groups + same-name
                                              suggestions
  monitor       recorder|saved on|off         start/stop a monitor/scanner
  pipeline      on|off                         start/stop the pipeline (stand-by)
  pipeline      convert|upload on|off          tick/untick a stage (any time)
  open                                          launch the Scr33nX app
  close                                         gracefully quit the Scr33nX app

  Ranking note: a rank only applies to a model that's in Saved Models or the
  Recorder. `rank` on a model that's on neither list returns an error — use
  `add-saved <model> --rank N` to save AND rate in one call.

EXAMPLES
  python scr33nx_ctl.py record "https://chaturbate.com/name/"
  python scr33nx_ctl.py stop name --site stripchat
  python scr33nx_ctl.py add-saved "stripchat.com/name" --rank 5
  python scr33nx_ctl.py rank name 4 --site chaturbate
  python scr33nx_ctl.py stop-all
  python scr33nx_ctl.py clear
  python scr33nx_ctl.py dashboard
  python scr33nx_ctl.py models --recording
  python scr33nx_ctl.py models --online --min-rank 4
  python scr33nx_ctl.py link "chaturbate.com/alice" "stripchat.com/alicexx"
  python scr33nx_ctl.py link alice bobby --site-a chaturbate --site-b stripchat
  python scr33nx_ctl.py unlink alice --site chaturbate
  python scr33nx_ctl.py links
  python scr33nx_ctl.py monitor recorder on
  python scr33nx_ctl.py pipeline on
  python scr33nx_ctl.py pipeline convert on
  python scr33nx_ctl.py pipeline upload off
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


def _api_token() -> str:
    """Optional shared secret for the local API (Settings → Local API).
    Sources, in order: SCR33NX_TOKEN env var, then api_token in the app's
    own config file — so the CLI keeps working with zero setup when the
    user sets a token in the app."""
    tok = os.environ.get("SCR33NX_TOKEN", "").strip()
    if tok:
        return tok
    cfg = os.path.join(os.path.expanduser("~"), ".streamrecorder_config.json")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            return str(json.load(f).get("api_token", "") or "").strip()
    except Exception:
        return ""


_TOKEN = _api_token()


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
    if _TOKEN:
        req.add_header("X-Api-Token", _TOKEN)
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


def _wait_in(name: str, site: str, key: str, deadline_s: float = 8.0) -> bool:
    """Poll /status until `key` (in_recorder / in_saved) is true — the add runs
    on the app's UI thread, so membership isn't instant."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if _request("GET", f"/status?name={name}&site={site}").get(key):
            return True
        time.sleep(0.3)
    return False


def _apply_rank(name: str, site: str, rank: int) -> dict:
    return _request("POST", "/rank",
                    {"name": name, "site": site, "rank": int(rank)})


def cmd_status(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("GET", f"/status?name={name}&site={site}")


def cmd_rank(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    # The app rejects ranking a model that's on neither list; relay that error.
    return _apply_rank(name, site, a.value)


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
    if getattr(a, "rank", None) is not None:
        # Model is in the Recorder by now, so ranking is allowed.
        steps.append({"rank": _apply_rank(name, site, a.rank)})
    return {"ok": True, "name": name, "site": site, "steps": steps,
            "final_status": _request("GET", f"/status?name={name}&site={site}")}


def cmd_stop(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/record",
                    {"name": name, "site": site, "action": "stop"})


def _add(a, target: str, member_key: str):
    """Add to `target` ('recorder'/'saved'); with --rank, also set the star
    rank once the model is on the list. A plain add (no --rank) behaves exactly
    as before."""
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    add = _request("POST", "/add",
                   {"name": name, "site": site, "target": target})
    rank = getattr(a, "rank", None)
    if rank is None:
        return add
    # An "already present" add is fine — we still want to (re)rank it.
    already = (not add.get("ok")) and "already" in str(add.get("error", "")).lower()
    if not add.get("ok") and not already:
        return {"ok": False, "name": name, "site": site,
                "error": add.get("error", "add failed"), "steps": [{"add": add}]}
    _wait_in(name, site, member_key)
    rk = _apply_rank(name, site, rank)
    return {"ok": bool(rk.get("ok")), "name": name, "site": site,
            "steps": [{"add": add}, {"rank": rk}],
            "final_status": _request("GET", f"/status?name={name}&site={site}")}


def cmd_add_recorder(a):
    return _add(a, "recorder", "in_recorder")


def cmd_add_saved(a):
    return _add(a, "saved", "in_saved")


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


def cmd_clear(a):
    return _request("POST", "/clear")


def cmd_dashboard(a):
    return _request("GET", "/dashboard")


def cmd_models(a):
    """One bulk /models call, filtered locally — lets the bot answer
    "who's recording?" / "which of my 4★+ models are live?" in one shot."""
    res = _request("GET", "/models")
    if not res.get("ok"):
        return res
    ms = res.get("models", [])
    if a.site:
        ms = [m for m in ms if m.get("site") == a.site]
    if a.recording:
        ms = [m for m in ms if m.get("status") == "recording"]
    elif a.online:   # "online" = live right now, so recording counts too
        ms = [m for m in ms if m.get("status") in ("online", "recording")]
    if a.min_rank:
        ms = [m for m in ms if int(m.get("rank") or 0) >= a.min_rank]
    return {"ok": True, "recording": res.get("recording", 0),
            "count": len(ms), "models": ms}


def cmd_link(a):
    na, sa, err = _resolve(a.target_a, a.site_a)
    if err:
        return err
    nb, sb, err = _resolve(a.target_b, a.site_b)
    if err:
        return err
    return _request("POST", "/link", {"a": {"name": na, "site": sa},
                                      "b": {"name": nb, "site": sb}})


def cmd_unlink(a):
    name, site, err = _resolve(a.target, a.site)
    if err:
        return err
    return _request("POST", "/unlink", {"name": name, "site": site})


def cmd_links(a):
    return _request("GET", "/links")


def cmd_monitor(a):
    return _request("POST", "/monitor",
                    {"target": a.which, "enabled": a.state == "on"})


def cmd_pipeline(a):
    # pipeline on|off          → start/stop the whole pipeline
    # pipeline convert on|off  → tick/untick the Convert stage
    # pipeline upload  on|off  → tick/untick the Upload stage
    if a.state in ("on", "off"):
        return _request("POST", "/pipeline", {"enabled": a.state == "on"})
    if a.state2 not in ("on", "off"):
        return {"ok": False, "error": f"usage: pipeline {a.state} on|off"}
    return _request("POST", "/pipeline/stage", {a.state: a.state2 == "on"})


def _api_up() -> bool:
    """True if Scr33nX's API answers (i.e. the app is running)."""
    s = _request("GET", "/status?name=__probe__&site=chaturbate")
    return s.get("in_recorder") is not None


def cmd_open(a):
    if _api_up():
        return {"ok": True, "note": "Scr33nX is already running"}
    # Launcher lives in the project root, one level up from src/.
    bat = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "Scr33nX.bat")
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


def _rank_arg(v: str) -> int:
    try:
        iv = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError("rank must be an integer 0-5")
    if not 0 <= iv <= 5:
        raise argparse.ArgumentTypeError("rank must be 0-5 (0 clears)")
    return iv


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
    s.add_argument("--rank", type=_rank_arg, default=None,
                   help="also set a 0-5 star rank")
    s.set_defaults(fn=cmd_record)
    s = sub.add_parser("stop");          add_target(s); s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("add-recorder");  add_target(s)
    s.add_argument("--rank", type=_rank_arg, default=None,
                   help="also set a 0-5 star rank")
    s.set_defaults(fn=cmd_add_recorder)
    s = sub.add_parser("add-saved");     add_target(s)
    s.add_argument("--rank", type=_rank_arg, default=None,
                   help="also set a 0-5 star rank")
    s.set_defaults(fn=cmd_add_saved)
    s = sub.add_parser("rank");          add_target(s)
    s.add_argument("value", type=_rank_arg, help="0-5 star rank (0 clears)")
    s.set_defaults(fn=cmd_rank)
    s = sub.add_parser("remove");        add_target(s); s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("remove-saved");  add_target(s); s.set_defaults(fn=cmd_remove_saved)
    s = sub.add_parser("auto");          add_target(s)
    s.add_argument("state", choices=("on", "off"))
    s.set_defaults(fn=cmd_auto)
    s = sub.add_parser("stop-all");      s.set_defaults(fn=cmd_stop_all)
    s = sub.add_parser("clear");         s.set_defaults(fn=cmd_clear)
    s = sub.add_parser("dashboard");     s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("models")
    s.add_argument("--site", default="", choices=("",) + SITES,
                   help="only models on one site")
    s.add_argument("--recording", action="store_true",
                   help="only models recording right now")
    s.add_argument("--online", action="store_true",
                   help="only live models (online or recording)")
    s.add_argument("--min-rank", type=_rank_arg, default=0,
                   help="only models ranked N+ stars")
    s.set_defaults(fn=cmd_models)
    s = sub.add_parser("link")
    s.add_argument("target_a", help="first model (URL or username)")
    s.add_argument("target_b", help="second model (URL or username)")
    s.add_argument("--site-a", default="", choices=("",) + SITES,
                   help="site for a bare first username")
    s.add_argument("--site-b", default="", choices=("",) + SITES,
                   help="site for a bare second username")
    s.set_defaults(fn=cmd_link)
    s = sub.add_parser("unlink");        add_target(s); s.set_defaults(fn=cmd_unlink)
    s = sub.add_parser("links");         s.set_defaults(fn=cmd_links)
    s = sub.add_parser("monitor")
    s.add_argument("which", choices=("recorder", "saved"))
    s.add_argument("state", choices=("on", "off"))
    s.set_defaults(fn=cmd_monitor)
    s = sub.add_parser("pipeline")
    s.add_argument("state", choices=("on", "off", "convert", "upload"))
    s.add_argument("state2", nargs="?", choices=("on", "off"))
    s.set_defaults(fn=cmd_pipeline)
    s = sub.add_parser("open");  s.set_defaults(fn=cmd_open)
    s = sub.add_parser("close"); s.set_defaults(fn=cmd_close)

    args = p.parse_args()
    res = args.fn(args)
    print(json.dumps(res))
    return 0 if res.get("ok", False) or res.get("in_recorder") is not None else 1


if __name__ == "__main__":
    sys.exit(main())
