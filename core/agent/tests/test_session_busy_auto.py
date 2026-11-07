"""Live app.dev 2026-10-15 (user 2, the call chat pchat-mv9qx0lo, generation g2): the post-meeting
recap ran in the chat's `-auto` sibling and held the conversation's head lease; the founder's
"finalize the meeting" was answered 409 `session_conflict` three times in the 52 seconds it ran.

(1) A message whose chat's OWN turn holds the head waits behind it and runs the moment the head is
    free — for up to `QUEUE_WAIT_S`, then it is refused, typed. Another participant is still refused.
(2) The head lease is a heartbeat: a short TTL renewed only while the holder's workload runs, so a
    dead turn frees the conversation within `LEASE_TTL_S`, not the hour the fixed TTL held it.
"""
from __future__ import annotations

import time

import fakeredis
import pytest

from tests._session_fakes import MemBlobs
from control_plane import session_sync
from control_plane.session_store import SessionStore
from shared import units
from shared.units import output_topic


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def _r():
    return fakeredis.FakeRedis(decode_responses=True)


# ── (2) the lease is a heartbeat ──────────────────────────────────────────────────────────────────

def test_a_lease_lives_seconds_not_an_hour():
    r = _r()
    assert session_sync.take_lease(r, "s-1", "u1")
    assert 0 < r.ttl(session_sync.lease_key("s-1")) <= 90
    assert session_sync.LEASE_TTL_S <= 120 < session_sync.QUEUE_WAIT_S + 1


def _watched(store, r, alive):
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic, alive=alive)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    session_sync.take_lease(r, sid, "u1")
    r.expire(session_sync.lease_key(sid), 10)
    saver._watch["u1"]["since"] -= session_sync.LEASE_RENEW_S + 1   # a renewal is due
    return sid, saver


def test_the_saver_renews_the_lease_while_the_turn_runs(store):
    r = _r()
    sid, saver = _watched(store, r, alive=lambda uid: True)
    saver.poll_once()
    assert r.ttl(session_sync.lease_key(sid)) > 10
    assert r.get(session_sync.lease_key(sid)) == "u1"


def test_a_dead_turns_lease_is_not_renewed_and_lapses(store):
    r = _r()
    sid, saver = _watched(store, r, alive=lambda uid: False)
    saver.poll_once()
    assert r.ttl(session_sync.lease_key(sid)) <= 10


def test_an_unanswerable_probe_does_not_keep_a_lease_alive(store):
    r = _r()

    def boom(uid):
        raise ConnectionError()

    sid, saver = _watched(store, r, alive=boom)
    saver.poll_once()
    assert r.ttl(session_sync.lease_key(sid)) <= 10


def test_a_completed_turn_still_releases_at_once(store):
    import json
    r = _r()
    sid, saver = _watched(store, r, alive=lambda uid: True)
    r.xadd(output_topic("u1"), {"event": json.dumps({"type": "turn-complete"})})
    saver.poll_once()
    assert not r.exists(session_sync.lease_key(sid))
    assert not r.exists(session_sync.lease_kind_key(sid))


def test_the_dispatcher_gives_the_saver_the_runtime_as_its_probe(store, tmp_path, monkeypatch):
    from tests.test_session_sync import _full_dispatcher
    d, _, saver = _full_dispatcher(tmp_path, store, monkeypatch)
    monkeypatch.setattr(d, "_workload_gone", lambda uid: uid == "dead")
    assert saver._alive("live") is True and saver._alive("dead") is False


# ── (1) a message waits behind its own chat's turn ─────────────────────────────────────────────

def _inv(subject, session, text="hello"):
    return units.make_dispatch(subject=subject, trigger="message",
                               start={"entrypoint": {"inline": text}},
                               context={"kind": "none", "session": session})


def test_the_chats_own_auto_turn_makes_the_refusal_queueable(store, tmp_path, monkeypatch):
    from tests.test_session_sync import _full_dispatcher
    d, r, _ = _full_dispatcher(tmp_path, store, monkeypatch)
    sid = session_sync.bundle_for(store, "alice", "pchat-x")["id"]
    auto = units.routine_unit_id("alice", "pchat-x", None)
    assert session_sync.take_lease(r, sid, auto, kind="meeting_recap")
    inv = _inv("alice", "pchat-x", "finalize the meeting")
    with pytest.raises(session_sync.SessionBusy) as exc:
        d.dispatch(inv)
    assert exc.value.holder == auto and exc.value.holder_kind == "meeting_recap"
    assert exc.value.queueable_for(units.dispatch_id(inv))


def test_another_participants_turn_is_still_refused(store, tmp_path, monkeypatch):
    from tests.test_session_sync import _full_dispatcher
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    d, r, _ = _full_dispatcher(tmp_path, store, monkeypatch)
    d.dispatch(_inv("alice", sid, "@vexa one"))
    bob = _inv("bob", sid, "@vexa two")
    with pytest.raises(session_sync.SessionBusy) as exc:
        d.dispatch(bob)
    assert not exc.value.queueable_for(units.dispatch_id(bob))


def test_same_chat_is_the_unit_without_generation_and_routine_suffix():
    a = "agent-2-chat-pchat-mv9qx0lo-g2"
    assert session_sync.same_chat(a, a + "-auto")
    assert session_sync.same_chat(a, "agent-2-chat-pchat-mv9qx0lo-g1")
    assert not session_sync.same_chat(a, "agent-3-chat-pchat-mv9qx0lo-g2")
    assert not session_sync.same_chat(a, "")


def _busy(r, sid="s-1", holder="agent-2-chat-p-g2-auto", kind="meeting_recap"):
    session_sync.take_lease(r, sid, holder, kind=kind)
    with pytest.raises(session_sync.SessionBusy) as exc:
        session_sync.take_lease(r, sid, "agent-2-chat-p-g2")
    return exc.value


def test_a_queued_message_runs_the_moment_the_head_is_free():
    from control_plane.routers import chats
    r = _r()
    busy = _busy(r)
    calls = []

    def dispatch():
        calls.append(time.time())
        return "agent-2-chat-p-g2"

    w = chats.queue_behind(r, busy, "agent-2-chat-p-g2", dispatch, lambda: "5-0",
                           session="pchat-p", key=("agent-2-chat-p-g2", "c-1"), poll_s=0.02)
    time.sleep(0.2)
    assert not w.done.is_set() and calls == []                 # waits while the recap runs
    assert chats.queued_waiter(("agent-2-chat-p-g2", "c-1")) is w   # a retry attaches to it
    session_sync.release_lease(r, "s-1", "agent-2-chat-p-g2-auto")
    assert w.done.wait(2)
    assert len(calls) == 1 and w.error is None and w.start == "5-0"
    assert chats.queued_waiter(("agent-2-chat-p-g2", "c-1")) is None


def test_a_dead_holders_lapsed_lease_lets_the_message_run():
    from control_plane.routers import chats
    r = _r()
    busy = _busy(r)
    w = chats.queue_behind(r, busy, "agent-2-chat-p-g2", lambda: "agent-2-chat-p-g2",
                           lambda: None, session="pchat-p", poll_s=0.02)
    r.delete(session_sync.lease_key("s-1"))                      # the TTL ran out
    assert w.done.wait(2) and w.error is None


def test_a_message_queued_past_the_bound_is_refused_typed_with_stop():
    from control_plane.routers import chats
    r = _r()
    busy = _busy(r)
    w = chats.queue_behind(r, busy, "agent-2-chat-p-g2", lambda: pytest.fail("dispatched"),
                           lambda: None, session="pchat-p", wait_s=0.1, poll_s=0.02)
    assert w.done.wait(2)
    f = w.error["fault"]
    assert (f["source"], f["kind"], f["action"], f["status"]) == ("session-store", "session_conflict", "stop", 409)
    assert "five minutes" in w.error["message"]


def test_the_stream_says_what_it_waits_behind_then_runs_the_turn():
    from control_plane.routers import chats
    r = _r()
    busy = _busy(r)
    w = chats.queue_behind(r, busy, "agent-2-chat-p-g2", lambda: "agent-2-chat-p-g2",
                           lambda: "7-0", session="pchat-p", poll_s=0.02)
    events = chats.queued_events(w, busy.holder_kind,
                                 lambda uid, st: iter([{"type": "turn-accepted", "at": st}]), tick_s=0.05)
    first = next(events)
    assert first == {"type": "queued", "holder_kind": "meeting_recap",
                     "message": "Finishing the meeting recap — your message runs next."}
    session_sync.release_lease(r, "s-1", "agent-2-chat-p-g2-auto")
    rest = [e for e in events if e is not None]
    assert rest == [{"type": "turn-accepted", "at": "7-0"}]
