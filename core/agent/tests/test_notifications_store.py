"""The notification store (#1783): one row per recipient in the session store, the leak rule in the
composer, coalescing, the keep bound, mark-read, and live delivery onto the recipient's own channel."""
from __future__ import annotations

import json

import pytest

from control_plane import notifications as nt
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs


@pytest.fixture
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


class _Bus:
    def __init__(self):
        self.sent = []

    def __call__(self, ch, payload):
        self.sent.append((ch, json.loads(payload)))


def _clock(start=1000.0):
    t = {"now": start}

    def now():
        return t["now"]
    now.t = t
    return now


def test_a_row_is_stored_per_recipient_and_published_to_their_own_channel(store):
    bus = _Bus()
    n = nt.Notifier(store, publish=bus)
    rows = n.emit(["bob", "carol"], kind="chat_added", actor="alice", actor_name="Alice",
                  object_kind="chat", object_id="s-1", object_name="Q3 planning")
    assert len(rows) == 2
    assert rows[0]["text"] == "Alice added you to the chat “Q3 planning”"
    assert rows[0]["object"] == {"kind": "chat", "id": "s-1", "name": "Q3 planning"}
    assert [c for c, _ in bus.sent] == ["u:bob:notifications", "u:carol:notifications"]
    assert bus.sent[0][1]["type"] == "notification" and bus.sent[0][1]["notification"]["read"] is False


def test_per_recipient_isolation_list_count_and_mark(store):
    n = nt.Notifier(store)
    [mine] = n.emit(["bob"], kind="chat_added", actor="alice", actor_name="Alice",
                    object_kind="chat", object_id="s-1", object_name="One")
    n.emit(["carol"], kind="chat_added", actor="alice", actor_name="Alice",
           object_kind="chat", object_id="s-2", object_name="Two")
    assert [x["object"]["id"] for x in n.listing("bob")["notifications"]] == ["s-1"]
    assert n.listing("mallory") == {"notifications": [], "unread": 0}
    # carol cannot mark bob's row, by id or by "all"
    assert n.mark_read("carol", [mine["id"]]) == {"marked": 0, "unread": 1}
    n.mark_read("carol", every=True)
    assert n.unread_count("bob") == 1
    assert n.mark_read("bob", [mine["id"]]) == {"marked": 1, "unread": 0}


def test_the_actor_is_never_notified_of_their_own_act(store):
    n = nt.Notifier(store)
    assert n.emit(["alice"], kind="call_started", actor="alice", actor_name="Alice",
                  object_kind="chat", object_id="s-1", object_name="x") == []


def test_the_leak_rule_text_has_only_the_actor_and_the_object(store):
    # there is no parameter that could carry a message body; names are one line, bounded
    n = nt.Notifier(store)
    [row] = n.emit(["bob"], kind="chat_mentioned", actor="alice", actor_name="Alice\nSmith",
                   object_kind="chat", object_id="s-1", object_name="x" * 300)
    assert "\n" not in row["text"] and len(row["object"]["name"]) <= nt.NAME_MAX
    assert set(row) == {"id", "kind", "actor", "object", "text", "created", "read", "actions"}
    with pytest.raises(TypeError):
        n.emit(["bob"], kind="chat_mentioned", actor="alice", object_kind="chat", object_id="s",
               body="the secret message")  # type: ignore[call-arg]


def test_an_unknown_kind_records_nothing_and_never_raises(store):
    n = nt.Notifier(store)
    assert n.emit(["bob"], kind="pwned", actor="a", object_kind="chat", object_id="s") == []
    assert n.unread_count("bob") == 0


def test_a_repeat_while_unread_refreshes_one_row(store):
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    a = n.emit(["bob"], kind="chat_mentioned", actor="alice", actor_name="Alice",
               object_kind="chat", object_id="s-1", object_name="x")[0]
    clock.t["now"] += 30
    b = n.emit(["bob"], kind="chat_mentioned", actor="alice", actor_name="Alice",
               object_kind="chat", object_id="s-1", object_name="x")[0]
    assert a["id"] == b["id"] and b["created"] == a["created"] + 30
    assert n.unread_count("bob") == 1
    n.mark_read("bob", every=True)
    c = n.emit(["bob"], kind="chat_mentioned", actor="alice", actor_name="Alice",
               object_kind="chat", object_id="s-1", object_name="x")[0]
    assert c["id"] != a["id"]


def test_newest_first_and_unread_only(store):
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    for i in range(3):
        clock.t["now"] += 1
        n.emit(["bob"], kind="chat_added", actor="alice", actor_name="A", object_kind="chat",
               object_id=f"s-{i}", object_name=str(i))
    got = n.listing("bob")["notifications"]
    assert [x["object"]["id"] for x in got] == ["s-2", "s-1", "s-0"]
    n.mark_read("bob", [got[0]["id"]])
    assert [x["object"]["id"] for x in n.listing("bob", unread_only=True)["notifications"]] == ["s-1", "s-0"]


def test_old_read_rows_are_dropped_past_the_keep_bound_unread_never(store, monkeypatch):
    monkeypatch.setattr(nt, "KEEP", 3)
    clock = _clock()
    n = nt.Notifier(store, clock=clock)
    for i in range(3):
        clock.t["now"] += 1
        n.emit(["bob"], kind="chat_added", actor="a", object_kind="chat", object_id=f"s-{i}", object_name="x")
    n.mark_read("bob", every=True)
    for i in range(3, 6):
        clock.t["now"] += 1
        n.emit(["bob"], kind="chat_added", actor="a", object_kind="chat", object_id=f"s-{i}", object_name="x")
    ids = [x["object"]["id"] for x in n.listing("bob")["notifications"]]
    assert ids == ["s-5", "s-4", "s-3"]


def test_a_failed_publish_keeps_the_row(store):
    def boom(ch, payload):
        raise ConnectionError("redis down")
    n = nt.Notifier(store, publish=boom)
    assert len(n.emit(["bob"], kind="chat_added", actor="a", object_kind="chat", object_id="s", object_name="x")) == 1
    assert n.unread_count("bob") == 1


def test_rail_frames_are_published_and_not_stored(store):
    bus = _Bus()
    n = nt.Notifier(store, publish=bus)
    n.rail(["bob"], session_id="s-1", change="removed")
    assert bus.sent == [("u:bob:notifications", {"type": "rail", "change": "removed", "session": "s-1"})]
    assert n.unread_count("bob") == 0


def test_the_table_is_in_the_session_store_family_so_its_role_is_granted_it():
    from control_plane import session_schema
    assert "agent_session_notification" in session_schema.TABLES
