"""ADR-0048 decisions 6 and 7 in the session store: a call held in a session, and the changes
proposed after it that the meeting's owner decides."""
from __future__ import annotations

import pytest

from tests._session_fakes import MemBlobs
from control_plane.session_store import SessionDenied, SessionStore


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 'sessions.db'}",
                          MemBlobs())
    yield s
    s.close()


def _shared(store):
    sid = store.create("alice", name="team")["id"]
    store.add_participant(sid, "alice", "bob", role="member")
    store.add_participant(sid, "alice", "vic", role="viewer")
    return sid


def test_a_member_binds_a_call_and_it_is_the_sessions_active_call(store):
    sid = _shared(store)
    store.bind_call(sid, room="call-aaaa", meeting_id=7, owner="bob", share_token="7.x")
    store.bind_call(sid, room="call-bbbb", meeting_id=8, owner="alice", share_token="8.y")
    assert store.active_call(sid)["room"] == "call-aaaa"
    assert store.call_by_meeting(8)["session_id"] == sid
    store.end_call(sid, "call-aaaa")
    assert store.active_call(sid)["room"] == "call-bbbb"
    store.end_call(sid, "call-bbbb")
    assert store.active_call(sid) is None


def test_a_viewer_or_stranger_cannot_bind_a_call(store):
    sid = _shared(store)
    for who in ("vic", "eve"):
        with pytest.raises(SessionDenied):
            store.bind_call(sid, room="call-cccc", meeting_id=9, owner=who, share_token="9.z")


def test_only_the_meeting_owner_decides_a_proposal_and_only_once(store):
    sid = _shared(store)
    p = store.propose(sid, owner="bob", kind="page", workspace="acme", path="notes/call.md",
                      title="Call notes", body="# Notes\n", meeting_id=7)
    assert p["status"] == "open" and store.proposals(sid)[0]["id"] == p["id"]
    with pytest.raises(SessionDenied):
        store.decide(p["id"], "alice", accept=True)          # the session owner is not the meeting owner
    done = store.decide(p["id"], "bob", accept=True, body="# Notes (edited)\n")
    assert done["status"] == "accepted" and done["decided_by"] == "bob"
    assert done["body"] == "# Notes (edited)\n"
    with pytest.raises(SessionDenied):
        store.decide(p["id"], "bob", accept=False)


def test_a_proposal_is_validated(store):
    sid = _shared(store)
    with pytest.raises(ValueError):
        store.propose(sid, owner="bob", kind="page", workspace="acme", path="../escape.md", body="x")
    with pytest.raises(ValueError):
        store.propose(sid, owner="bob", kind="page", workspace=None, path="a.md", body="x")
    with pytest.raises(ValueError):
        store.propose(sid, owner="bob", kind="weird", body="x")
    c = store.propose(sid, owner="bob", kind="commitment", body="Bob sends the deck by Friday")
    assert c["workspace"] is None and c["kind"] == "commitment"
