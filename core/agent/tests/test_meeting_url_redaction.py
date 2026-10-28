"""The worker's meeting-link logs name the meeting without its passcode.

A chat message that is a meeting link goes to the vexa MCP (`worker/meeting_link.py`). When the
check fails or the bot is refused, the worker logs the link and the refusal detail; agent-api logs
ship off-box, so the Zoom `?pwd=` / Teams `?p=` in them is masked while the host and meeting id stay.
The vector table every redactor shares is checked here against agent's copy.
"""
from __future__ import annotations

import json
import logging
import pathlib

import httpx
import pytest

from shared import meeting_url_redaction as red
from worker import meeting_link

SECRET = "TESTPASSCODE"
ZOOM = f"https://us05web.zoom.us/j/81234567890?pwd={SECRET}"
VECTORS = json.loads((pathlib.Path(__file__).resolve().parent / "fixtures"
                      / "meeting-url-redaction-vectors.json").read_text())


@pytest.mark.parametrize("case", VECTORS["redact_url"], ids=lambda c: c["why"])
def test_redact_url_vectors(case):
    assert red.redact_url(case["in"]) == case["out"]


@pytest.mark.parametrize("case", VECTORS["redact_urls_in"], ids=lambda c: c["why"])
def test_redact_urls_in_vectors(case):
    assert red.redact_urls_in(case["in"]) == case["out"]


class _Caller:
    def __init__(self, parse=None, send=None):
        self.parse, self.send = parse, send

    def call(self, name, args):
        fn = self.parse if name == "parse_meeting_link" else self.send
        return fn(args)


def _raise(exc):
    def f(_args):
        raise exc
    return f


def _logged(caplog):
    return "\n".join(r.getMessage() for r in caplog.records)


def test_positive_control_the_capture_sees_a_raw_link(caplog):
    caplog.set_level(logging.WARNING, logger="agent_api.worker")
    logging.getLogger("agent_api.worker").warning("meeting link %s", ZOOM)
    assert SECRET in _logged(caplog)


def test_unchecked_link_tool_error_is_logged_redacted(caplog):
    caplog.set_level(logging.WARNING, logger="agent_api.worker")
    meeting_link.act(ZOOM, _Caller(parse=_raise(meeting_link.ToolError(f"upstream 502 for {ZOOM}"))))
    out = _logged(caplog)
    assert SECRET not in out and "us05web.zoom.us/j/81234567890" in out


def test_unreachable_link_is_logged_redacted(caplog):
    caplog.set_level(logging.WARNING, logger="agent_api.worker")
    meeting_link.act(ZOOM, _Caller(parse=_raise(httpx.ConnectError(f"connect failed: {ZOOM}"))))
    out = _logged(caplog)
    assert SECRET not in out and "us05web.zoom.us/j/81234567890" in out


def test_refused_bot_is_logged_redacted(caplog):
    caplog.set_level(logging.WARNING, logger="agent_api.worker")
    parsed = json.dumps({"platform": "zoom", "native_meeting_id": "81234567890"})
    meeting_link.act(ZOOM, _Caller(
        parse=lambda a: parsed,
        send=_raise(meeting_link.ToolError(
            f"HTTP 422\n'native_meeting_id' is required: meeting_url '{ZOOM}' (unrecognized)"))))
    out = _logged(caplog)
    assert "refused" in out
    assert SECRET not in out and "us05web.zoom.us/j/81234567890" in out
