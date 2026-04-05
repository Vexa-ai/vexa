"""Config management — ~/.vexa/config.json + env var overrides."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".vexa"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS = {
    "endpoint": "http://localhost:8100",
    "api_key": "",
    "user_id": "",
    "default_model": None,
}


def load() -> dict:
    """Load config from file, override with env vars."""
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    # Env vars override file
    if v := os.getenv("VEXA_ENDPOINT"):
        cfg["endpoint"] = v
    if v := os.getenv("VEXA_API_KEY"):
        cfg["api_key"] = v
    if v := os.getenv("VEXA_USER_ID"):
        cfg["user_id"] = v
    return cfg


def save(cfg: dict):
    """Save config to ~/.vexa/config.json with 600 permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    CONFIG_FILE.chmod(0o600)
