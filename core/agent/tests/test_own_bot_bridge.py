"""The own-bot bridge (``eval/replay/own_bot_bridge.py``) fans only what the collector admitted.

Every bot appends to the one ``transcription_segments`` stream, and only meeting-api's collector can
check that an entry was signed by the meeting's own bot; it writes what it admitted to
``tc:meeting:{meeting_id}``. The bridge follows that feed for its one meeting, so a raw entry that
names the meeting without its bot's signature never reaches the bridge's output. Offline: fakeredis.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import fakeredis
import pytest

BRIDGE = Path(__file__).resolve().parents[1] / "eval" / "replay" / "own_bot_bridge.py"


def _load():
    spec = importlib.util.spec_from_file_location("own_bot_bridge", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def r(monkeypatch):
    import redis

    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: fakeredis.FakeRedis(server=server, decode_responses=True))
    monkeypatch.setenv("VEXA_API_KEY", "k")
    return fakeredis.FakeRedis(server=server, decode_responses=True)


def _wire(text, sid, *, completed=True):
    return {"payload": json.dumps({"type": "transcription", "session_uid": "abc-defg-hij", "meeting_id": "abc-defg-hij",
                                   "segments": [{"speaker": "A", "text": text, "start": 10.0, "end": 11.0,
                                                 "completed": completed, "segment_id": sid}]})}


def _run(monkeypatch, *argv):
    bridge = _load()
    monkeypatch.setattr(sys, "argv", ["own_bot_bridge.py", "--meeting-url", "https://meet.google.com/abc-defg-hij",
                                      *argv])
    bridge.KEY = "k"
    bridge.main()


def test_only_the_collectors_feed_reaches_the_output(r, monkeypatch):
    # a raw entry for meeting 42 that its bot did not sign: the collector dropped it
    r.xadd("transcription_segments", {"payload": json.dumps({
        "type": "transcription", "meeting_id": 42,
        "segments": [{"text": "forged", "completed": True, "start": 0, "end": 1, "segment_id": "f"}]})})
    r.xadd("transcription_segments", {"payload": json.dumps({"type": "session_end", "meeting_id": 42})})
    # what the collector admitted for meeting 42
    r.xadd("tc:meeting:42", _wire("hello", "s1"))
    r.xadd("tc:meeting:42", {"payload": json.dumps({"type": "session_end", "session_uid": "abc-defg-hij"})})
    _run(monkeypatch, "--no-bot", "--meeting-id", "42")
    out = [json.loads(f["payload"]) for _id, f in r.xrange("tc:meeting:abc-defg-hij")]
    texts = [s["text"] for p in out for s in p.get("segments", [])]
    assert texts == ["hello"]
    assert out[-1]["type"] == "session_end"


def test_without_a_meeting_id_there_is_no_feed_to_follow(r, monkeypatch):
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--no-bot")
