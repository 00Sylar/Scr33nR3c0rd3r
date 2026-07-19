"""
audit.py — persistent model-audit trail.

Appends one JSON line per membership/rank event to
%LOCALAPPDATA%\\Scr33nX\\models_audit.log (rotating, 2 MB x 3) so list
changes can be reconstructed later ("when did X disappear from Saved?",
"who cleared that rank — me or the bot?"). Shared by the classic Tk app,
the web UI, and the port-5200 API (via the `source` field).

Never raises: auditing must not be able to break a user action.

Line format (one JSON object per line):
  {"ts": "2026-07-16T12:34:56", "action": "rank_change", "name": "...",
   "site": "chaturbate", "source": "ui", "old": 3, "new": 5}

Actions: startup, recorder_add, recorder_remove, saved_add, saved_remove,
rank_change, vip_add, vip_remove, import, export, clear_recorder.
Sources: ui | api | import | clear.
"""

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Same safe location as streamrecorder.log — never inside the (possibly
# cloud-synced) app folder, where sync locks break log rotation.
_LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA")
                        or os.path.expanduser("~"), "Scr33nX")
AUDIT_FILE = os.path.join(_LOG_DIR, "models_audit.log")

_logger = None
_lock_guard = False  # once handler setup failed, don't retry every event


def _get_logger():
    global _logger, _lock_guard
    if _logger is None and not _lock_guard:
        lg = logging.getLogger("scr33nx.audit")
        lg.setLevel(logging.INFO)
        lg.propagate = False   # keep raw JSON lines out of the main app log
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
            h = RotatingFileHandler(AUDIT_FILE, maxBytes=2_000_000,
                                    backupCount=3, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(h)
            _logger = lg
        except OSError:
            _lock_guard = True   # disk/permission problem — disable quietly
    return _logger


def log_event(action: str, name: str = "", site: str = "",
              source: str = "ui", **extra):
    """Append one audit line. Silently a no-op on any failure."""
    try:
        lg = _get_logger()
        if lg is None:
            return
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "action": action}
        if name:
            rec["name"] = name
        if site:
            rec["site"] = site
        rec["source"] = source
        for k, v in extra.items():
            if v is not None:
                rec[k] = v
        lg.info(json.dumps(rec, ensure_ascii=False))
    except Exception:
        pass
