"""ADR-0048 step B, agent-api's half — a chat turn carries its conversation in, and what the worker
hands back is saved as the next version whether or not anybody watches the turn."""
from __future__ import annotations

import base64
import json

import fakeredis
import pytest

from tests._session_fakes import MemBlobs
from control_plane import session_blobs, session_sync
from control_plane.session_store import SessionDenied, SessionStore
from shared.units import output_topic


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def test_own_thread_is_created_on_first_use(store):
    b = session_sync.bundle_for(store, "alice", "pchat-1")
    assert b["version"] == 0 and b["files"] == {} and b["id"].startswith("s-")
    assert store.by_name("alice", "pchat-1")["id"] == b["id"]


def test_a_store_id_is_answered_only_for_a_participant(store):
    sid = store.create("alice", name="main")["id"]
    assert session_sync.bundle_for(store, "mallory", sid) is None
    store.add_participant(sid, "alice", "bob", role="member")
    assert session_sync.bundle_for(store, "bob", sid)["id"] == sid


def test_a_viewer_cannot_take_a_turn(store):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "vic", role="viewer")
    with pytest.raises(SessionDenied):
        session_sync.bundle_for(store, "vic", sid)


def _ev(sid, base, files, turn="t1"):
    return {"event": json.dumps({"type": "session-bundle", "session_id": sid, "base": base, "turn_id": turn,
                                 "files": {k: base64.b64encode(v).decode() for k, v in files.items()}})}


def test_saver_saves_what_the_worker_hands_back(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    r.xadd(output_topic("u1"), {"event": json.dumps({"type": "message-delta", "text": "hi"})})
    r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"1"}))
    r.xadd(output_topic("u1"), {"event": json.dumps({"type": "turn-complete", "turn_id": "t1"})})
    assert saver.poll_once() == 1
    assert store.load_bundle(sid)["files"] == {"a": b"1"}
    assert saver.watching() == []          # every handed-over turn completed: watch dropped


def test_saver_reads_only_after_the_hand_over_tail(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    old = r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"stale"}))   # before this turn
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", old, session_id=sid, subject="alice", base=0)
    r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"fresh"}))
    saver.poll_once()
    assert store.load_bundle(sid)["files"] == {"a": b"fresh"}


def test_a_conflicting_hand_back_is_refused_and_logged(store, caplog):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    store.save_bundle(sid, 0, {"a": b"winner"})
    r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"loser"}))
    assert saver.poll_once() == 0
    assert store.load_bundle(sid)["files"] == {"a": b"winner"}
    assert "refused" in caplog.text and "loser" not in caplog.text


def test_dispatcher_puts_the_bundle_on_the_turn_entry(store, tmp_path, monkeypatch):
    from control_plane.dispatch import Dispatcher
    from shared import unit_input
    from shared.config import load_settings
    from shared.units import input_topic, make_dispatch

    settings = load_settings(workspaces_dir=str(tmp_path / "ws"), internal_api_secret="x" * 32)
    d = Dispatcher(settings, runtime=None, identity=None)
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(d, "_redis", lambda: r)
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    d.attach_sessions(store, saver)
    inv = make_dispatch(subject="alice", trigger="message",
                        start={"entrypoint": {"inline": "hello"}}, context={"session": "pchat-2"})
    d._predeliver("u9", inv, "n1")
    (eid, fields), = r.xrange(input_topic("u9"))
    entry = unit_input.verified_turn(d._unit_input_key("u9"), fields)
    assert entry["prompt"] == "hello"
    assert "session_bundle" not in entry and entry["session_bundle_ref"]   # by reference
    from shared import stream_bounds
    stream_bounds.resolve_in_bundle(r.get, entry)
    assert entry["session_bundle"]["id"] == store.by_name("alice", "pchat-2")["id"]
    assert saver.watching() == ["u9"]
    bound = saver._watch["u9"]
    assert (bound["session_id"], bound["subject"], bound["base"]) == (entry["session_bundle"]["id"], "alice", 0)


def test_canonical_name_is_one_literal_on_both_sides():
    from worker import session_carry
    assert session_sync.CANONICAL == session_carry.CANONICAL


def test_history_reads_the_stored_conversation(store):
    import hashlib
    sid = store.create("alice", name="main")["id"]
    assert session_sync.history(store, store.get(sid)) is None      # nothing stored: old layout
    prompt = "[preamble]\nwhat is the plan?"
    transcript = "\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"content": prompt}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Here it is."}]}},
    ])
    store.save_bundle(sid, 0, {
        ".claude/sessions/conversation.session": b"sid-1\n",
        ".claude/sessions/conversation.turns.jsonl": (json.dumps(
            {"key": hashlib.sha256(prompt.encode()).hexdigest(), "user_text": "what is the plan?"}) + "\n").encode(),
        ".claude/projects/-w/sid-1.jsonl": transcript.encode(),
    })
    turns = session_sync.history(store, store.get(sid))
    assert [t["role"] for t in turns] == ["user", "agent"]
    assert turns[0]["user_text"] == "what is the plan?" and turns[1]["text"] == "Here it is."


# ── S6: the dispatcher never hands a shared turn the old layout on a store error ──────────────
def _dispatcher(tmp_path, store, monkeypatch):
    from control_plane.dispatch import Dispatcher
    from shared.config import load_settings

    settings = load_settings(workspaces_dir=str(tmp_path / "ws"), internal_api_secret="x" * 32)
    d = Dispatcher(settings, runtime=None, identity=None)
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(d, "_redis", lambda: r)
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    d.attach_sessions(store, saver)
    return d, r, saver


def _inv(subject, session, text="hello"):
    from shared.units import make_dispatch
    return make_dispatch(subject=subject, trigger="message",
                         start={"entrypoint": {"inline": text}}, context={"kind": "none", "session": session})


def test_a_store_error_for_a_store_id_is_unavailable_not_the_old_layout(store, tmp_path, monkeypatch):
    sid = store.create("alice", name="main")["id"]
    d, _, _ = _dispatcher(tmp_path, store, monkeypatch)
    monkeypatch.setattr(store, "load_bundle", lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    with pytest.raises(session_sync.SessionUnavailable):
        d._session_bundle(_inv("alice", sid))
    # a private named thread still falls back
    assert d._session_bundle(_inv("alice", "pchat-3")) is None
    with pytest.raises(session_sync.SessionUnavailable):
        d._session_bundle(_inv("alice", "pchat-3"), shared=True)


# ── S4: the saver writes only the session it handed over, for a speaker who may still write it ──
def test_a_worker_naming_a_foreign_session_is_refused(store, caplog):
    r = fakeredis.FakeRedis(decode_responses=True)
    mine = store.create("alice", name="main")["id"]
    theirs = store.create("carol", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=mine, subject="alice", base=0)
    r.xadd(output_topic("u1"), _ev(theirs, 0, {"a": b"injected"}))
    assert saver.poll_once() == 0
    assert store.load_bundle(theirs) == {"version": 0, "files": {}}
    assert "another session" in caplog.text


def test_a_hand_back_on_a_base_it_was_not_handed_is_refused(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    store.save_bundle(sid, 0, {"a": b"1"})
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    r.xadd(output_topic("u1"), _ev(sid, 1, {"a": b"claims the head"}))
    assert saver.poll_once() == 0
    assert store.load_bundle(sid)["files"] == {"a": b"1"}


def test_a_speaker_removed_mid_turn_is_not_saved(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u2", "0-0", session_id=sid, subject="bob", base=0)
    store.remove_participant(sid, "alice", "bob")
    r.xadd(output_topic("u2"), _ev(sid, 0, {"a": b"after removal"}))
    assert saver.poll_once() == 0
    assert store.load_bundle(sid)["version"] == 0


def test_two_queued_turns_on_one_unit_both_save(store):
    """Turn 2 was handed the same head as turn 1 (turn 1 had not saved yet); the worker kept its
    newer local copy and hands back on the version turn 1 produced."""
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"1"}))
    r.xadd(output_topic("u1"), _ev(sid, 1, {"a": b"2"}))
    assert saver.poll_once() == 2
    assert store.load_bundle(sid)["files"] == {"a": b"2"}


# ── S5: one turn per session at a time; a refused save is said, never silently dropped ────────
class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _full_dispatcher(tmp_path, store, monkeypatch):
    from control_plane.dispatch import Dispatcher
    from shared.config import load_settings

    settings = load_settings(workspaces_dir=str(tmp_path / "ws"), internal_api_secret="x" * 32)
    d = Dispatcher(settings, _Runtime(), _Identity())
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(d, "_redis", lambda: r)
    monkeypatch.setattr(d, "_watch_delivery", lambda *a, **k: None)
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    d.attach_sessions(store, saver)
    return d, r, saver


def test_a_second_concurrent_turn_in_a_session_is_refused_up_front(store, tmp_path, monkeypatch):
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    d, r, saver = _full_dispatcher(tmp_path, store, monkeypatch)
    ua = d.dispatch(_inv("alice", sid, "@vexa one"))
    with pytest.raises(session_sync.SessionBusy) as exc:
        d.dispatch(_inv("bob", sid, "@vexa two"))
    assert exc.value.kind == "session_conflict"
    # the same unit may queue behind its own turn
    assert d.dispatch(_inv("alice", sid, "@vexa three")) == ua
    # every turn the lease covered completes: the session is free again
    r.xadd(output_topic(ua), {"event": json.dumps({"type": "turn-complete"})})
    r.xadd(output_topic(ua), {"event": json.dumps({"type": "turn-complete"})})
    saver.poll_once()
    assert d.dispatch(_inv("bob", sid, "@vexa two")) != ua


def test_a_refused_save_is_recorded_as_a_typed_session_conflict(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)
    store.save_bundle(sid, 0, {"a": b"other turn"})
    r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"this turn"}))
    saver.poll_once()
    f = session_sync.recorded_fault(r, sid)
    assert f["kind"] == "session_conflict" and f["head"] == 1 and f["base"] == 0
    # the next save that lands clears it
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=1)
    r.xadd(output_topic("u1"), _ev(sid, 1, {"a": b"next"}))
    saver.poll_once()
    assert session_sync.recorded_fault(r, sid) is None


def test_after_a_refused_save_the_next_hand_over_replaces_the_worker_copy(store):
    """S5 with the worker: a refused hand-back must not leave the worker's copy ahead of the head
    for ever. The next bundle is marked ``replace``, the saver expects that head again, and the
    worker's next hand-back on it is saved (and clears the fault)."""
    from worker.session_carry import CANONICAL, Carry  # noqa: PLC0415

    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    b = session_sync.bundle_for(store, "alice", "main", faults=r)
    assert "replace" not in b
    import tempfile, pathlib  # noqa: E401, PLC0415
    root = pathlib.Path(tempfile.mkdtemp()) / "carry"
    carry = Carry(root, session="main")
    carry.receive(b)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=b["version"])
    store.save_bundle(sid, 0, {f".claude/sessions/{CANONICAL}.session": b"other\n"})  # another writer
    (root / ".claude/sessions").mkdir(parents=True, exist_ok=True)
    (root / ".claude/sessions/main.session").write_bytes(b"mine\n")
    r.xadd(output_topic("u1"), {"event": json.dumps(carry.bundle_event())})
    saver.poll_once()
    assert session_sync.recorded_fault(r, sid)["kind"] == "session_conflict"
    b2 = session_sync.bundle_for(store, "alice", "main", faults=r)
    assert b2["replace"] is True and b2["version"] == 1
    carry.receive(b2)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=b2["version"], replace=True)
    (root / ".claude/sessions/main.session").write_bytes(b"mine again\n")
    r.xadd(output_topic("u1"), {"event": json.dumps(carry.bundle_event())})
    assert saver.poll_once() == 1
    assert session_sync.recorded_fault(r, sid) is None


def test_a_store_that_fails_a_save_records_a_fault(store, monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=0)

    def _down(*a, **k):
        raise OSError("store down")

    monkeypatch.setattr(store, "save_bundle", _down)
    r.xadd(output_topic("u1"), _ev(sid, 0, {"a": b"1"}))
    saver.poll_once()
    f = session_sync.recorded_fault(r, sid)
    assert f["kind"] == "session_unavailable" and f["base"] == 0


def test_a_replace_hand_over_moves_the_expected_base_back(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id="s-x", subject="alice", base=3)
    saver.watch("u1", "0-0", session_id="s-x", subject="alice", base=1)
    assert saver._watch["u1"]["base"] == 3
    saver.watch("u1", "0-0", session_id="s-x", subject="alice", base=1, replace=True)
    assert saver._watch["u1"]["base"] == 1


# ── a one-person chat never asks its person to resend (2026-10-13, app.dev) ─────────────────────
T = ".claude/projects/-w/sid.jsonl"


def test_in_a_one_person_chat_a_hand_back_that_extends_the_head_is_saved_onto_it(store):
    """The worker's copy ran ahead of the head (its earlier hand-backs were not taken); its
    transcript still starts with everything stored, so in a chat only one person writes it is the
    conversation, one turn longer — saved onto the head, never answered with "send it again"."""
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    store.save_bundle(sid, 0, {T: b"turn1\n"})                       # head v1
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=1)
    r.xadd(output_topic("u1"), _ev(sid, 3, {T: b"turn1\nturn2\nturn3\n"}))   # worker thinks v3
    assert saver.poll_once() == 1
    got = store.load_bundle(sid)
    assert got["version"] == 2 and got["files"][T] == b"turn1\nturn2\nturn3\n"
    assert session_sync.recorded_fault(r, sid) is None


def test_a_hand_back_that_does_not_extend_the_head_is_still_refused(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    store.save_bundle(sid, 0, {T: b"turn1\nturn2\n"})
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=1)
    r.xadd(output_topic("u1"), _ev(sid, 5, {T: b"turn1\nother\n"}))   # a stale, diverged copy
    assert saver.poll_once() == 0
    assert store.load_bundle(sid)["files"][T] == b"turn1\nturn2\n"
    assert session_sync.recorded_fault(r, sid)["kind"] == "session_conflict"


def test_with_two_people_a_mismatched_base_is_never_retried(store):
    r = fakeredis.FakeRedis(decode_responses=True)
    sid = store.create("alice", name="main")["id"]
    store.add_participant(sid, "alice", "bob")
    store.save_bundle(sid, 0, {T: b"turn1\n"})
    saver = session_sync.SessionSaver(store, r, output_topic=output_topic)
    saver.watch("u1", "0-0", session_id=sid, subject="alice", base=1)
    r.xadd(output_topic("u1"), _ev(sid, 3, {T: b"turn1\nturn2\n"}))
    assert saver.poll_once() == 0


def test_a_stale_queued_delivery_flagged_replace_never_winds_the_worker_back(tmp_path):
    """Queued messages each carry the head as of their dispatch. After a refusal they are flagged
    `replace`; one dispatched long ago must not wind the worker back past a newer head it was handed."""
    from worker.session_carry import CANONICAL, Carry  # noqa: PLC0415

    c = Carry(tmp_path / "carry", session="main")
    c.receive({"id": "s-1", "version": 40, "files": {f".claude/sessions/{CANONICAL}.session": base64.b64encode(b"v40\n").decode()}})
    c.receive({"id": "s-1", "version": 22, "replace": True,
               "files": {f".claude/sessions/{CANONICAL}.session": base64.b64encode(b"v22\n").decode()}})
    assert c.version == 40
    assert (tmp_path / "carry/.claude/sessions/main.session").read_bytes() == b"v40\n"


# ── the store is the only place a conversation lives (app.dev, 2026-10-13: 235 turn-record files
#    were still being written under _system by routine runs and other turns without a bundle) ────

def test_a_routine_run_in_its_thread_carries_the_conversation(store, tmp_path, monkeypatch):
    from shared.units import make_dispatch

    sid = store.create("alice", name="flow-abc")["id"]
    d, _, _ = _dispatcher(tmp_path, store, monkeypatch)
    inv = make_dispatch(subject="alice", trigger="scheduled", start={"entrypoint": {"inline": "run"}},
                        context={"kind": "none", "session": sid})
    b = d._session_bundle(inv)
    assert b is not None and b["id"] == sid


def test_with_the_store_on_a_turn_without_a_conversation_never_writes_under_system(tmp_path, monkeypatch):
    """A turn that carries no conversation (no bundle) still keeps its continuity outside every
    mount when the store is on — never in `_system`, where the agent's tools could read it."""
    from worker import engine

    system = tmp_path / "system"
    system.mkdir()
    monkeypatch.setattr(engine, "active_mounts", lambda: [{"slug": "_system", "path": str(system), "role": "system"}])
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VEXA_SESSION_STORE", "1")
    engine.set_carry(None)
    root = engine._continuity_root(tmp_path / "work")
    assert system not in root.parents and root != system
    assert str(root).startswith(str(tmp_path / "home"))
    monkeypatch.delenv("VEXA_SESSION_STORE")
    assert engine._continuity_root(tmp_path / "work") == system     # store off: the old layout
