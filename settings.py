"""
settings.py — Persist user configuration to JSON

Main config (models, output dir, etc.):  ~/.streamrecorder_config.json
Pipeline/Telegram config:                <project>/Pipeline/pipeline_settings.json
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional, List


CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".streamrecorder_config.json")

# Pipeline settings live alongside the standalone pipeline.py so the app UI
# and the standalone script share one source of truth.
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_CONFIG_FILE = os.path.join(_PROJECT_DIR, "Pipeline", "pipeline_settings.json")

_PIPELINE_KEYS = (
    "pipeline_enabled",
    "pipeline_converted_dir",
    "telegram_api_id",
    "telegram_api_hash",
    "telegram_group_id",
    "telegram_topic_id",
    "telegram_session_dir",
)


@dataclass
class AppSettings:
    output_dir: str = os.path.join(os.path.expanduser("~"), "Videos", "StreamRecorder")
    max_size_mb: Optional[int] = None          # None = unlimited
    check_interval: int = 30                   # seconds
    minimize_to_tray: bool = False
    notifications_enabled: bool = True
    gap_warnings_enabled: bool = True          # toast when segments are dropped
    privacy_mode_enabled: bool = False         # idle screen cover (starfield)
    max_quality: int = 0                       # global cap: variant height px (0 = unlimited)
    auto_downgrade_enabled: bool = False       # step struggling streams down a quality rung
    playwright_fallback_enabled: bool = True   # Stripchat: use browser fallback when native fails
    models: List[dict] = None                  # [{name, site, auto_rec, max_q}, ...]
    saved_models: List[dict] = None            # [{name, site}, ...]  view-only list
    # Pipeline / Telegram upload — persisted to PIPELINE_CONFIG_FILE
    pipeline_enabled: bool = False
    pipeline_converted_dir: str = ""           # empty = <output_dir>/converted
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_group_id: str = ""                # e.g. -1001234567890
    telegram_topic_id: str = ""                # "0" or empty = no topic
    telegram_session_dir: str = ""             # tdlib db folder; empty = ./Pipeline/.tdlib

    def __post_init__(self):
        if self.models is None:
            self.models = []
        if self.saved_models is None:
            self.saved_models = []


def _load_pipeline_file() -> dict:
    if not os.path.exists(PIPELINE_CONFIG_FILE):
        return {}
    try:
        with open(PIPELINE_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


def load_settings() -> AppSettings:
    main = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                main = json.load(f) or {}
        except Exception:
            main = {}

    s = AppSettings()
    s.output_dir = main.get("output_dir", s.output_dir)
    s.max_size_mb = main.get("max_size_mb", s.max_size_mb)
    s.check_interval = main.get("check_interval", s.check_interval)
    s.minimize_to_tray = main.get("minimize_to_tray", s.minimize_to_tray)
    s.notifications_enabled = main.get("notifications_enabled", s.notifications_enabled)
    s.gap_warnings_enabled = main.get("gap_warnings_enabled", s.gap_warnings_enabled)
    s.privacy_mode_enabled = main.get("privacy_mode_enabled", s.privacy_mode_enabled)
    s.max_quality = main.get("max_quality", s.max_quality)
    s.auto_downgrade_enabled = main.get("auto_downgrade_enabled", s.auto_downgrade_enabled)
    s.playwright_fallback_enabled = main.get("playwright_fallback_enabled", s.playwright_fallback_enabled)
    s.models = main.get("models", [])
    s.saved_models = main.get("saved_models", [])

    # Pipeline config: prefer Pipeline/pipeline_settings.json; migrate legacy
    # fields from the main config file on first load if present.
    pipe = _load_pipeline_file()
    legacy_present = any(k in main for k in _PIPELINE_KEYS)
    if not pipe and legacy_present:
        pipe = {k: main[k] for k in _PIPELINE_KEYS if k in main}
        _save_json(PIPELINE_CONFIG_FILE, pipe)

    s.pipeline_enabled        = pipe.get("pipeline_enabled", s.pipeline_enabled)
    s.pipeline_converted_dir  = pipe.get("pipeline_converted_dir", "")
    s.telegram_api_id         = pipe.get("telegram_api_id", "")
    s.telegram_api_hash       = pipe.get("telegram_api_hash", "")
    s.telegram_group_id       = pipe.get("telegram_group_id", "")
    s.telegram_topic_id       = pipe.get("telegram_topic_id", "")
    s.telegram_session_dir    = pipe.get("telegram_session_dir", "")

    # Strip legacy pipeline fields from main config on next save
    if legacy_present:
        save_settings(s)
    return s


def save_settings(s: AppSettings):
    data = {
        "output_dir": s.output_dir,
        "max_size_mb": s.max_size_mb,
        "check_interval": s.check_interval,
        "minimize_to_tray": s.minimize_to_tray,
        "notifications_enabled": s.notifications_enabled,
        "gap_warnings_enabled": s.gap_warnings_enabled,
        "privacy_mode_enabled": s.privacy_mode_enabled,
        "max_quality": s.max_quality,
        "auto_downgrade_enabled": s.auto_downgrade_enabled,
        "playwright_fallback_enabled": s.playwright_fallback_enabled,
        "models": s.models,
        "saved_models": s.saved_models,
    }
    _save_json(CONFIG_FILE, data)
    save_pipeline_settings(s)


def save_pipeline_settings(s: AppSettings):
    data = {
        "pipeline_enabled":        s.pipeline_enabled,
        "pipeline_converted_dir":  s.pipeline_converted_dir,
        "telegram_api_id":         s.telegram_api_id,
        "telegram_api_hash":       s.telegram_api_hash,
        "telegram_group_id":       s.telegram_group_id,
        "telegram_topic_id":       s.telegram_topic_id,
        "telegram_session_dir":    s.telegram_session_dir,
    }
    _save_json(PIPELINE_CONFIG_FILE, data)
