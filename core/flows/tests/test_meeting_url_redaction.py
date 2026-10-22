"""A routine that sends a bot names the meeting in its chat and in its step errors without the
passcode, and keeps the meeting id the passcode rides behind.

`request_meeting_bot` says one line in the routine's chat ("Sent the bot to …") and stores its
result; `dispatch_bot` stores the meeting id it spawned for. A Zoom link carries `?pwd=`, a Teams
link `?p=`: the chat line and the error keep the host and path, and the stored native id is the
path segment alone. The vector table every redactor shares is checked here against flows' copy.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import Reaction, StepCtx, StepError  # noqa: E402
from flows_steps import grants  # noqa: E402
from flows_steps import meeting as mt  # noqa: E402
import meeting_url_redaction as red  # noqa: E402

SECRET = "S3cr3tPasscode"
ZOOM = f"https://us05web.zoom.us/j/81234567890?pwd={SECRET}"
TEAMS = f"https://teams.microsoft.com/meet/3741234567890?p={SECRET}"
VECTORS = json.loads((Path(__file__).resolve().parent / "fixtures" / "meeting-url-redaction-vectors.json").read_text())


@pytest.mark.parametrize("case", VECTORS["redact_url"], ids=lambda c: c["why"])
def test_redact_url_vectors(case):
    assert red.redact_url(case["in"]) == case["out"]


@pytest.mark.parametrize("case", VECTORS["redact_urls_in"], ids=lambda c: c["why"])
def test_redact_urls_in_vectors(case):
    assert red.redact_urls_in(case["in"]) == case["out"]


def _register():
    steps = {}

    class Reg:
        def step(self, **_kw):
            def deco(fn):
                steps[fn.__name__] = fn
                return fn
            return deco
    mt.register_user_steps(Reg())
    return steps["request_meeting_bot"]


def _ctx(url):
    flow = SimpleNamespace(owner="7", name="r", scope="instance", version=1, ident="r",
                           param=lambda k, d="": {"meeting_url": url}.get(k, d))
    r = Reaction("rid", "sid", "clock", {}, "r", 1, "request_meeting_bot", "running", 1, 0.0,
                 None, None, None)
    return StepCtx(reaction=r, effect_key="rid:request_meeting_bot", prior={}, clock_now=1.0,
                   scratch={}, flow=flow)


@pytest.fixture
def said(monkeypatch):
    lines = []
    monkeypatch.setattr(mt, "_say", lambda ctx, uid, text: lines.append(text))
    monkeypatch.setattr(mt, "door_headers", lambda uid=None: {"X-Flows-Service-Key": "k"})
    monkeypatch.setattr(grants, "effective", lambda flow, uid: {"bot.request"})
    return lines


def test_positive_control_the_capture_sees_a_raw_url(said):
    mt._say(None, "7", f"Sent the bot to {ZOOM}")
    assert SECRET in said[0]


@pytest.mark.parametrize("url,keep", [(ZOOM, "us05web.zoom.us/j/81234567890"),
                                      (TEAMS, "teams.microsoft.com/meet/3741234567890")])
def test_sent_line_keeps_the_meeting_and_hides_the_passcode(monkeypatch, said, url, keep):
    monkeypatch.setattr(mt, "http", lambda *a, **k: (201, {"id": 5, "native_meeting_id": "x",
                                                           "platform": "zoom"}))
    _register()(_ctx(url))
    assert said and SECRET not in said[0] and keep in said[0]


def test_already_there_stores_the_meeting_id_alone(monkeypatch, said):
    monkeypatch.setattr(mt, "http", lambda *a, **k: (409, {"detail": "exists"}))
    ctx = _ctx(ZOOM)
    done = _register()(ctx)
    assert done.result["native"] == "81234567890"
    assert SECRET not in json.dumps(ctx.scratch) and SECRET not in said[0]


def test_refusal_echoing_the_link_is_redacted_in_chat_and_error(monkeypatch, said):
    monkeypatch.setattr(mt, "http", lambda *a, **k: (
        422, {"detail": f"meeting_url '{ZOOM}' (unrecognized meeting link)"}))
    with pytest.raises(StepError) as e:
        _register()(_ctx(ZOOM))
    assert SECRET not in str(e.value) and "81234567890" in str(e.value)
    assert SECRET not in said[0]


def test_dispatch_bot_native_id_drops_the_query(monkeypatch):
    monkeypatch.setattr(mt, "door_headers", lambda uid=None: {"X-Flows-Service-Key": "k"})
    monkeypatch.setattr(mt, "http", lambda *a, **k: (201, {"id": 41}))
    r = Reaction("rid", "sid", "meeting.started", {"uid": "7", "url": ZOOM}, "invite_intake", 3,
                 "dispatch_bot", "running", 1, 0.0, None, None, None)
    ctx = StepCtx(reaction=r, effect_key="rid:dispatch_bot", prior={"ensure_user": {"uid": "7"}},
                  clock_now=1_000_000.0, scratch={})
    done = mt.dispatch_bot(ctx)
    assert done.result["native"] == "81234567890"
