"""D16 (PRD 0.13.4): a notice is a notification row with ``expires_at``. Once it passes, the notice is
gone from every read (list, count, get) and the next write sweeps it; acts that wait for an answer
(an invite) never expire on their own."""
from __future__ import annotations

import pytest

from control_plane import notifications as nt
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs


@pytest.fixture
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def _clock(start=1000.0):
    t = {"now": start}

    def now():
        return t["now"]
    now.t = t
    return now


def test_a_notice_expires_from_every_read(store):
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    (row,) = n.emit(["bob"], kind="call_started", actor="alice", actor_name="Alice", object_kind="chat",
                    object_id="s-1", object_name="Standup")
    assert n.unread_count("bob") == 1 and n.get("bob", row["id"]) is not None
    clock.t["now"] += nt.DEFAULT_TTL["call_started"] + 1
    assert n.unread_count("bob") == 0
    assert n.listing("bob")["notifications"] == []
    assert n.get("bob", row["id"]) is None


def test_an_invite_waits_and_a_caller_may_set_its_own_expiry(store):
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    n.emit(["bob"], kind="workspace_invited", actor="alice", actor_name="Alice", object_kind="workspace",
           object_id="team", object_name="Team")
    n.emit(["bob"], kind="workspace_added", actor="alice", actor_name="Alice", object_kind="workspace",
           object_id="ops", object_name="Ops", expires_in=60)
    clock.t["now"] += 120
    assert [r["kind"] for r in n.listing("bob")["notifications"]] == ["workspace_invited"]


def test_the_next_write_sweeps_expired_rows(store):
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    n.emit(["bob"], kind="chat_added", actor="alice", object_kind="chat", object_id="a", expires_in=10)
    clock.t["now"] += 11
    n.emit(["bob"], kind="chat_added", actor="alice", object_kind="chat", object_id="b")
    with store._lock:
        left = store._x("SELECT object_id FROM agent_session_notification WHERE recipient = 'bob'").fetchall()
    assert [r[0] for r in left] == ["b"]


def test_agent_notices_expire_by_default(store):
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    n.agent_post("alice", ["bob"], text="Heads up", source="r", source_name="Routine", object_kind="chat",
                 object_id="s-1")
    assert n.unread_count("bob") == 1
    clock.t["now"] += nt.DEFAULT_TTL["agent"] + 1
    assert n.unread_count("bob") == 0
