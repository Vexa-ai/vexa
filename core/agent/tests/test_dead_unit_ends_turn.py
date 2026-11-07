"""A TURN WHOSE WORKER IS GONE ENDS, VISIBLY (app.dev 2026-10-16: every new worker died at start; the
founder's chat said "still working" for 15+ minutes while agent-api logged "unit is not running; its
head lease is left to lapse" and did nothing else).

Once the saver has seen the unit dead on two probes in a row it stops watching it, frees the head
at once, and records why on the unit; an open view of that unit then says "that reply was
interrupted — send it again" and closes, so the next message starts a fresh turn."""
from __future__ import annotations

import json
import sys
import time

import fakeredis
import pytest

from tests._session_fakes import MemBlobs
from control_plane import session_sync, unit_faults
from control_plane.session_store import SessionStore
from shared.units import output_topic


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def _watched(store, r, alive):
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic, alive=alive)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    session_sync.take_lease(r, sid, "u1")
    return sid, saver


def _due(saver):
    saver._watch["u1"]["since"] -= session_sync.LEASE_RENEW_S + 1
    saver._watch["u1"]["renewed"] = 0


def test_a_unit_seen_dead_twice_frees_the_chat_and_says_why(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid, saver = _watched(store, r, alive=lambda uid: False)
    _due(saver)
    saver.poll_once()
    assert "u1" in saver._watch                                  # one dead probe: maybe a cold start
    _due(saver)
    saver.poll_once()
    assert "u1" not in saver._watch
    assert not r.exists(session_sync.lease_key(sid))             # the next message can start
    f = unit_faults.live(r, "u1")
    assert f and f["kind"] == "unavailable" and "interrupted" in f["detail"]


def test_a_live_unit_is_never_failed(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid, saver = _watched(store, r, alive=lambda uid: True)
    for _ in range(3):
        _due(saver)
        saver.poll_once()
    assert "u1" in saver._watch and r.get(session_sync.lease_key(sid)) == "u1"
    assert unit_faults.live(r, "u1") is None


def test_an_open_view_of_a_dead_unit_ends_with_the_reason(monkeypatch):
    import shared.adapters as adapters
    r = fakeredis.FakeRedis(decode_responses=True)

    class _Mod:
        @staticmethod
        def from_url(_url, decode_responses=True):
            return r

    monkeypatch.setitem(sys.modules, "redis", _Mod)
    reader = adapters.RedisStreamReader("redis://x", block_ms=1, idle_giveup_ms=60000)
    view = reader.read("u1", resume="0-0")
    assert next(view) is None                                    # idle: a keepalive
    unit_faults.record(r, "u1", {"source": "runtime", "kind": "unavailable", "op": "status",
                                 "status": None, "detail": "that reply was interrupted", "remedy": "Send it again."})
    rest = [x for x in view]
    evs = [x[0] if isinstance(x, tuple) else x for x in rest if x is not None]
    assert evs[0]["type"] == "error" and evs[0]["fault"]["kind"] == "unavailable"
    assert evs[-1]["type"] == "turn-complete"


def test_an_old_fault_does_not_end_a_new_view(monkeypatch):
    import shared.adapters as adapters
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set(unit_faults.fault_key("u1"), json.dumps({"source": "runtime", "kind": "unavailable", "op": "status",
                                                   "detail": "old", "at": time.time() - 600, "cursor": ""}))

    class _Mod:
        @staticmethod
        def from_url(_url, decode_responses=True):
            return r

    monkeypatch.setitem(sys.modules, "redis", _Mod)
    view = adapters.RedisStreamReader("redis://x", block_ms=1, idle_giveup_ms=5).read("u1", resume="0-0")
    assert all(x is None for x in view)                          # keepalives, then the idle give-up
