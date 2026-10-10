"""An invited bot transcribes in the person's or the deployment's language, not in auto-detect.

The invite-by-email flow spawns its bot through `POST /bots` with the person's key and nothing but
the meeting link. That is the point: the transcription language (transcription-language.v1) is a
fact meeting-api resolves on every spawn path — the person's default from identity, then the
deployment's — so this step must not carry a language of its own, which would pin every invited bot
past both defaults. meeting-api's side (`test_transcription_language.py`) proves that a body with no
language gets the defaults; this pins that the flow's body is that body.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import Reaction, StepCtx  # noqa: E402
from flows_steps import meeting as mt  # noqa: E402

URL = "https://meet.google.com/abc-defg-hij"


def test_the_invited_bot_is_spawned_with_the_link_alone(monkeypatch):
    sent = []
    monkeypatch.setattr(mt, "user_api_key", lambda uid: "k")

    def fake_http(method, url, headers, body=None, timeout=20):
        sent.append((method, url, body))
        return 201, {"id": 41, "native_meeting_id": "abc-defg-hij", "platform": "google_meet"}

    monkeypatch.setattr(mt, "http", fake_http)
    r = Reaction("rid", "sid", "meeting.started", {"uid": "7", "url": URL}, "invite_intake", 3,
                 "dispatch_bot", "running", 1, 0.0, None, None, None)
    ctx = StepCtx(reaction=r, effect_key="rid:dispatch_bot", prior={"ensure_user": {"uid": "7"}},
                  clock_now=1_000_000.0, scratch={})
    mt.dispatch_bot(ctx)
    (method, url, body), = sent
    assert method == "POST" and url.endswith("/bots")
    assert body == {"meeting_url": URL}
    assert "language" not in body and "allowed_languages" not in body
