"""ADR-0048 decisions 4 and 5 applied to one message: the session gate, when the agent speaks,
and the leak rule — including the founder's scenario."""
from __future__ import annotations

import pytest

from tests._session_fakes import MemBlobs
from control_plane import session_blobs
from control_plane.session_store import SessionDenied, SessionStore
from control_plane.session_turn import plan_turn

MEMBERS = {"team": {"alice", "bob"}, "alice-pricing": {"alice"}}


def _is_member(ws, who):
    return who in MEMBERS.get(ws, ())


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def _plan(store, subject, session, text, **kw):
    return plan_turn(store, subject=subject, session=session, text=text,
                     candidates=["alice", "team", "alice-pricing", "_system", "_global"],
                     is_member=_is_member, **kw)


def test_a_private_chat_answers_everything_and_narrows_nothing(store):
    p = _plan(store, "alice", "main", "what next?")
    assert p.speak and p.scope is None and p.preamble == "" and p.gen == ""


def test_a_non_participant_is_refused(store):
    sid = store.create("alice", name="main")["id"]
    with pytest.raises(SessionDenied) as e:
        _plan(store, "mallory", sid, "hi")
    assert e.value.reason == "not_a_participant"


def test_a_viewer_cannot_post(store):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "vic", role="viewer")
    with pytest.raises(SessionDenied) as e:
        _plan(store, "vic", sid, "@vexa hi")
    assert e.value.reason == "session_read_only"


def test_two_people_the_agent_waits_for_a_mention(store):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    assert not _plan(store, "bob", sid, "I'll take the migration").speak
    assert _plan(store, "bob", sid, "@vexa what did we agree?").speak


def test_the_leak_rule_founder_scenario(store):
    """A asks in a shared chat about a page only A can read → the turn cannot reach it, and the
    agent is told to say it can't use it here and to offer A's private chat."""
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    p = _plan(store, "alice", "main", "@vexa what does my pricing page say?",
              names={"alice-pricing": "Alice's pricing page", "alice": "Alice's desk"})
    assert p.speak
    assert "alice-pricing" in p.scope.withheld and "alice" in p.scope.withheld
    assert p.scope.allowed == ["team", "_global"]
    assert "Alice's pricing page" not in p.preamble and "withheld" in p.preamble
    assert "can't use it here" in p.preamble and "private chat" in p.preamble
    assert p.gen.startswith("s")


def test_an_explicit_share_is_the_one_exception(store):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    p = _plan(store, "alice", "main", "@vexa compare with this",
              shared_items=[{"label": "pricing.md", "text": "Price: 10"}], speaker_name="Alice")
    assert "Alice shared" in p.preamble and "Price: 10" in p.preamble
    assert "alice-pricing" in p.scope.withheld          # the workspace itself stays withheld


def test_the_agent_hears_what_was_said_without_it(store):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    store.add_note(sid, "bob", "I'll take the migration")
    p = _plan(store, "alice", "main", "@vexa who owns the migration?", names={"bob": "Bob"})
    # fenced as untrusted (M3): one JSON line per message inside <room-messages>
    assert '{"author": "Bob", "text": "I\'ll take the migration"}' in p.preamble
    assert '<room-messages untrusted="true">' in p.preamble


def test_a_change_of_participants_moves_the_unit(store):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    g1 = _plan(store, "alice", "main", "@vexa hi").gen
    store.add_participant(sid, "alice", "carol")
    g2 = _plan(store, "alice", "main", "@vexa hi").gen
    assert g1 and g2 and g1 != g2
