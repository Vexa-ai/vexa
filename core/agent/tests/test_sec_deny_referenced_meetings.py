"""Deny tests — a meeting a message names by reference (`[[meeting:N]]`) enters a turn other people
read only when every reader may read it (security gate 0.13.4 re-run, N1 on the reference fold).

The reference fold brings the meeting's title, time, participants and raw transcript into the turn.
In a shared chat it takes the same access decision as the meeting focus fold (`_MeetingFold`): a
meeting another reader may not read is not folded, and it stands in unnamed. The speaker's own chat
folds exactly as before.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.workspace_reader import WorkspaceReader
from tests.test_r1_rooms import _h, world  # noqa: F401  (fixture)
from tests.test_session_routes import _FakeReader

ROWS = {
    "77": {"id": 77, "user_id": "alice", "status": "completed", "native_meeting_id": "zzq-row-77",
           "platform": "google_meet", "data": {"title": "Alice salary review",
                                               "participants": ["Alice", "HR partner Quentin"]}},
    "88": {"id": 88, "user_id": "alice", "status": "completed", "native_meeting_id": "zzq-row-88",
           "platform": "google_meet", "data": {"title": "Team standup notes"}},
}


def _app(world, readers):
    """``readers`` maps a meeting row id to the subjects its one access decision admits."""
    _client, store, calls, _spawned, root, dispatcher = world
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     meeting_owner_lookup=lambda uid, mid, workspaces=None: (
                         dict(ROWS[str(mid)]) if str(uid) in readers.get(str(mid), ()) else None))
    return TestClient(app, raise_server_exceptions=False), store, calls


def _ask(client, session, prompt):
    r = client.post("/api/chat/submit", headers=_h("alice"), json={
        "session": session, "prompt": prompt, "to_agent": True})
    return r


def _turn(calls):
    return calls[-1][0]["start"]["entrypoint"]["inline"]


def test_a_shared_chat_does_not_fold_a_referenced_meeting_another_reader_may_not_read(world, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,transcript_signals")
    client, store, calls = _app(world, readers={"77": {"alice"}})
    row = store.create("alice", name="shared-ref-1")
    store.add_participant(row["id"], "alice", "bob")
    r = _ask(client, "shared-ref-1", "[[meeting:77]] what was decided?")
    assert calls, r.text
    text = _turn(calls)
    assert "Alice salary review" not in text and "Quentin" not in text and "zzq-row-77" not in text
    assert "### [[meeting:77]]" not in text           # the stand-in names nothing of the meeting
    assert "Not readable by everyone in this conversation" in text
    assert "Not available to this person" not in text  # the speaker may read it; the turn may not
    # the same question in alice's own chat folds the meeting, as before
    _ask(client, "own-ref-1", "[[meeting:77]] what was decided?")
    own = _turn(calls)
    assert "Alice salary review" in own and "### [[meeting:77]]" in own
    assert "Not readable by everyone" not in own


def test_a_shared_chat_folds_a_referenced_meeting_every_reader_may_read(world, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,transcript_signals")
    client, store, calls = _app(world, readers={"77": {"alice", "bob"}})
    row = store.create("alice", name="shared-ref-2")
    store.add_participant(row["id"], "alice", "bob")
    _ask(client, "shared-ref-2", "[[meeting:77]] what was decided?")
    text = _turn(calls)
    assert "Alice salary review" in text and "Not readable by everyone" not in text
    # …and withholds it once a reader who may not read it joins
    store.add_participant(row["id"], "alice", "vic")
    _ask(client, "shared-ref-2", "[[meeting:77]] what was decided?")
    text = _turn(calls)
    assert "Alice salary review" not in text and "Not readable by everyone" in text


def test_each_referenced_meeting_takes_the_decision_on_its_own(world, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,transcript_signals")
    client, store, calls = _app(world, readers={"77": {"alice"}, "88": {"alice", "bob"}})
    row = store.create("alice", name="shared-ref-3")
    store.add_participant(row["id"], "alice", "bob")
    _ask(client, "shared-ref-3", "compare [[meeting:77]] with [[meeting:88]]")
    text = _turn(calls)
    assert "Team standup notes" in text and "### [[meeting:88]]" in text
    assert "Alice salary review" not in text and "### [[meeting:77]]" not in text
    assert "Not readable by everyone in this conversation" in text


def test_a_referenced_meeting_the_speaker_may_not_read_is_unavailable_not_withheld(world, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,transcript_signals")
    client, store, calls = _app(world, readers={"77": {"bob"}})
    row = store.create("alice", name="shared-ref-4")
    store.add_participant(row["id"], "alice", "bob")
    _ask(client, "shared-ref-4", "[[meeting:77]] what was decided?")
    text = _turn(calls)
    assert "Alice salary review" not in text
    assert "Not available to this person" in text
