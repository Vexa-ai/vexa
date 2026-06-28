"""Config — gateway URL + API key, the only two things the client needs to reach the edge.

Defaults match clients/terminal/.env.local so the client 'just works' against a local stack.
No secret is committed here; we only READ the developer's existing env file.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_GATEWAY = "http://127.0.0.1:18056"
ENV_FILE = Path(__file__).resolve().parents[2] / "terminal" / ".env.local"


def load_env() -> None:
    """Seed GATEWAY_URL / VEXA_API_KEY / VEXA_BOT_API_KEY from the terminal's .env.local if unset."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def gateway_url() -> str:
    raw = os.environ.get("GATEWAY_URL", DEFAULT_GATEWAY)
    return raw.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")


def api_key() -> str:
    return os.environ.get("VEXA_API_KEY") or os.environ.get("VEXA_BOT_API_KEY") or ""
