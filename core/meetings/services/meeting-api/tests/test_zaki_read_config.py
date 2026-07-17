"""Production wiring checks for the default-off Minutes read route."""
from __future__ import annotations

import json
from pathlib import Path

from meeting_api.__main__ import _zaki_read_token


TOKEN = "wp-m1-read-token-0123456789abcdef"


def test_production_read_token_is_declared_secret_and_default_off(monkeypatch):
    monkeypatch.delenv("ZAKI_READ_TOKEN_MINUTES", raising=False)
    assert _zaki_read_token() is None

    monkeypatch.setenv("ZAKI_READ_TOKEN_MINUTES", TOKEN)
    assert _zaki_read_token() == TOKEN

    config_path = Path(__file__).parents[1] / "src/meeting_api/config.v1.json"
    config = json.loads(config_path.read_text())
    declaration = next(
        row for row in config["keys"] if row["key"] == "ZAKI_READ_TOKEN_MINUTES"
    )
    assert declaration["class"] == "defaulted"
    assert declaration["secret"] is True
    assert declaration["targets"] == []
