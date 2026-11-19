"""A RELOAD MID-TURN RESUMES THE REPLY (founder 2026-10-16: reloading while the agent replied dropped
the streaming output). `running_turn_from` is where a client with no cursor attaches to see the turn
that is running from its start: the Stream id just before its `turn-accepted`."""
import json
import sys

import fakeredis

from control_plane import api_shared
from shared import units


def _with(r, monkeypatch):
    class _M:
        @staticmethod
        def from_url(_u, decode_responses=True):
            return r
    monkeypatch.setitem(sys.modules, "redis", _M)


def _add(r, unit, ev):
    return r.xadd(units.output_topic(unit), {"event": json.dumps(ev)})


def test_a_running_turn_is_watched_from_its_start(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    _with(r, monkeypatch)
    _add(r, "u1", {"type": "turn-accepted", "turn_id": "t0"})
    _add(r, "u1", {"type": "done", "turn_id": "t0"})
    before = _add(r, "u1", {"type": "turn-complete", "turn_id": "t0"})
    _add(r, "u1", {"type": "turn-accepted", "turn_id": "t1"})
    _add(r, "u1", {"type": "delta", "text": "Half a rep", "turn_id": "t1"})
    assert api_shared.running_turn_from("redis://x", "u1") == before


def test_nothing_running_is_none(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    _with(r, monkeypatch)
    _add(r, "u1", {"type": "turn-accepted", "turn_id": "t0"})
    _add(r, "u1", {"type": "turn-complete", "turn_id": "t0"})
    assert api_shared.running_turn_from("redis://x", "u1") is None
    assert api_shared.running_turn_from(None, "u1") is None


def test_a_turn_accepted_first_on_the_stream_starts_from_zero(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    _with(r, monkeypatch)
    _add(r, "u1", {"type": "turn-accepted", "turn_id": "t0"})
    assert api_shared.running_turn_from("redis://x", "u1") == "0-0"
