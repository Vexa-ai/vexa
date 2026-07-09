"""Critical-path determinism tests (see docs/CONTROL-PLANE.md §4).

Each critical path is proven the same way: the simplest perfect fixture in → frozen output, asserted
BYTE-IDENTICAL across two runs (same in ⇒ same out — the ``gate:replay`` determinism discipline). LLM
nondeterminism is removed by a stubbed ``card_turn``. This module makes the CP catalog legible and guards
the plumbing the SoC refactor touches; the per-path behavioural depth still lives in ``test_ingest.py``
(CP1), ``test_worker.py`` (CP3/CP4) and ``test_transcription_watcher.py`` (CP3 arm).
"""
from __future__ import annotations

import json

from control_plane.api import _fold_meeting_transcript, _meeting_grounding
from worker.worker import serve_meeting


class MeetingStream:
    """Deterministic in-memory transcript stream (same shape as test_worker's helper)."""

    def __init__(self, inbox):
        self.out = []
        self._inbox = list(inbox)

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        tname = next(iter(streams))
        if not self._inbox:
            return []
        batch, self._inbox = self._inbox, []
        return [(tname, list(batch))]


def _seg(speaker, text, completed=True):
    return {"speaker": speaker, "text": text, "completed": completed}


def _transcript(eid, *segs):
    return (eid, {"payload": json.dumps({"type": "transcription", "segments": list(segs)})})


def _stub_card_turn(segments):
    # deterministic: output depends only on the input segments, never a model call
    yield {"type": "card", "card": {"kind": "person", "title": "Raj", "body": "internal lead"}}
    yield {"type": "card", "card": {"kind": "action", "title": "Send SSO docs", "body": "by EOW"}}


def _run_cp4_once():
    s = MeetingStream(inbox=[_transcript(
        "1-0", _seg("Jane", "let's discuss pricing"), _seg("Raj", "we need SSO first"))])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="unit:u:out",
                  card_turn=_stub_card_turn, idle_ms=10)
    return s.out


def test_cp4_copilot_turn_byte_identical_across_runs():
    """CP4: fixture transcript → copilot turn → emitted events. Same in ⇒ same out, byte-identical."""
    run1, run2 = _run_cp4_once(), _run_cp4_once()
    assert json.dumps(run1, sort_keys=True) == json.dumps(run2, sort_keys=True)
    # not vacuously equal: the expected cards were actually emitted
    titles = {json.loads(f["event"]).get("card", {}).get("title")
              for _t, f in run1 if json.loads(f["event"]).get("type") == "card"}
    assert {"Raj", "Send SSO docs"} <= titles


# ── CP6: chat grounded in a live meeting by folding its redis transcript stream (cookbook #1) ───────

def _seed_transcript_stream(native, *payloads):
    """A fakeredis with the meeting's transcript stream tc:meeting:{native} pre-seeded — the SAME wire
    the live copilot tails (worker/meeting.py)."""
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
