"""Lite carries the booking cap to meeting-api (ADR-0052 §8).

`BOOKED_MAX_PER_ACCOUNT` is one value for the whole deployment (founder, 3 Nov 2026). The entrypoint
exports it, 500 when the container was given none, and meeting-api inherits it from supervisord.

Offline: the entrypoint's export line run as it ships, and the rendered supervisor config.
"""
from __future__ import annotations

import re
import subprocess

from test_runtime_environment import ENTRYPOINT, _rendered_programs


def _export(env: dict) -> str:
    m = re.search(r"^export BOOKED_MAX_PER_ACCOUNT=.*$", ENTRYPOINT.read_text(), flags=re.M)
    assert m, "the entrypoint does not export BOOKED_MAX_PER_ACCOUNT"
    script = f'{m.group(0)}\nprintf %s "$BOOKED_MAX_PER_ACCOUNT"'
    return subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", **env},
                          capture_output=True, text=True, check=True).stdout


def test_the_entrypoint_exports_the_cap_500_by_default():
    assert _export({}) == "500"
    assert _export({"BOOKED_MAX_PER_ACCOUNT": "600"}) == "600"


def test_meeting_api_inherits_the_cap():
    _cmd, env = _rendered_programs()["meeting-api"]
    assert env["BOOKED_MAX_PER_ACCOUNT"] == "value-of-BOOKED_MAX_PER_ACCOUNT"
