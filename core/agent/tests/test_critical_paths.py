"""Critical-path determinism tests (see docs/CONTROL-PLANE.md §4).

Each critical path is proven the same way: the simplest perfect fixture in → frozen output, asserted
BYTE-IDENTICAL across two runs (same in ⇒ same out — the ``gate:replay`` determinism discipline). LLM
This module makes the CP catalog legible and guards the plumbing the SoC refactor touches; the
per-path behavioural depth still lives in ``test_ingest.py`` (CP1) and
``test_transcription_watcher.py`` (CP3).

CP4 (the copilot turn) was removed with its subject: PRD decision 34 deleted the in-product
inference pipeline, so there is no card beat to prove deterministic.
"""
from __future__ import annotations

import json

from control_plane.api import _fold_meeting_transcript, _meeting_grounding


# ── CP6: chat grounded in a live meeting by folding its redis transcript stream (cookbook #1) ───────

def _seed_transcript_stream(native, *payloads):
    """A fakeredis with the meeting's transcript stream tc:meeting:{native} pre-seeded — the SAME
    wire the terminal's live view renders."""
    import fakeredis

    r = fakeredis.FakeRedis(decode_responses=True)
    for p in payloads:
        r.xadd(f"tc:meeting:{native}", {"payload": json.dumps(p)})
    return r


def _fake_url(r, monkeypatch):
    """Point ``redis.from_url`` at a pre-seeded fakeredis so _fold/_grounding read it (best-effort path)."""
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: r)
    return "redis://fake"


def test_cp6_fold_dedups_refining_drafts_and_orders(monkeypatch):
    """A refining live draft (same segment_id) is upserted in place — latest text wins, no duplicate —
    and segments keep arrival order. session_end is skipped."""
    r = _seed_transcript_stream(
        "abc-defg-hij",
        {"type": "transcription", "segments": [{"segment_id": "s1", "speaker": "Jane", "text": "let's discuss"}]},
        {"type": "transcription", "segments": [{"segment_id": "s1", "speaker": "Jane", "text": "let's discuss pricing"}]},
        {"type": "transcription", "segments": [{"segment_id": "s2", "speaker": "Raj", "text": "SSO first"}]},
        {"type": "session_end"},
    )
    url = _fake_url(r, monkeypatch)
    folded = _fold_meeting_transcript(url, "abc-defg-hij", limit=400)
    assert folded == "Jane: let's discuss pricing\nRaj: SSO first"


def test_cp6_meeting_grounding_folds_live_transcript(monkeypatch):
    """active=meeting → plain dispatch context (a chat turn, no serve), no tools, and the prompt is
    grounded with the meeting's live transcript folded from its redis stream."""
    r = _seed_transcript_stream(
        "abc-defg-hij",
        {"type": "transcription", "segments": [{"segment_id": "s1", "speaker": "Jane", "text": "ship it Friday"}]},
    )
    url = _fake_url(r, monkeypatch)
    ctx, tools, prompt = _meeting_grounding(
        {"kind": "meeting", "meeting": {"platform": "google_meet", "native_id": "abc-defg-hij"}},
        session="main", prompt="who spoke last?", redis_url=url)
    assert ctx == {"kind": "none", "session": "main"} and tools == []
    assert "Jane: ship it Friday" in prompt
    assert prompt.startswith("You are assisting in a live meeting (google_meet/abc-defg-hij).")
    assert prompt.endswith("who spoke last?")


def test_cp6_meeting_with_no_transcript_says_so(monkeypatch):
    """active=meeting but the stream is empty → the agent is told no transcript has been captured yet
    (so it never claims the meeting 'hasn't been processed' off a missing notes file)."""
    r = _seed_transcript_stream("empty-mtg")  # no entries
    url = _fake_url(r, monkeypatch)
    _ctx, tools, prompt = _meeting_grounding(
        {"kind": "meeting", "meeting": {"native_id": "empty-mtg"}},
        session="main", prompt="summary?", redis_url=url)
    assert tools == []
    assert "no transcript has been captured yet" in prompt


def test_cp6_no_active_meeting_is_plain_chat():
    """No active meeting → no tools, plain none-context, prompt untouched (no leakage, no redis read)."""
    ctx, tools, prompt = _meeting_grounding(None, session="main", prompt="hello", redis_url=None)
    assert ctx == {"kind": "none", "session": "main"} and tools == [] and prompt == "hello"
    # a non-meeting active tab is likewise plain
    ctx2, tools2, _ = _meeting_grounding({"kind": "file", "ref": "x.md"}, "main", "hi", redis_url=None)
    assert ctx2["kind"] == "none" and tools2 == []
