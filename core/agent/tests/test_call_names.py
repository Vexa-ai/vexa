"""A CALL IS NAMED AFTER WHO OR WHERE, and renamed by its topic (founder validation O1, 2026-10-18).

The founder pressed "Start a meeting" four times and Meetings read "# Vexa meeting · Personal" four
times: no who, no what. These pin the naming rule (`call_names`), its write on the two places a call
starts (`POST /api/sessions/{s}/call` in a room, `call_person`), the topic rename pass, the person's
own "Start a new one" (`fresh`) and the live call's "in now" (the people in the room, never the
listener). Synthetic people only.
"""
from __future__ import annotations

import pytest

from control_plane import call_names as cn
from tests.test_call_person import AGENT, _Rooms, _call, w  # noqa: F401 — the call_person rig


# ── the rule ────────────────────────────────────────────────────────────────────────────────────

def test_a_call_is_named_after_who_or_where():
    assert cn.person_call_name("Megan Ross") == "Call with Megan"
    assert cn.person_call_name("tester-b@acme.test") == "Call with tester-b"
    assert cn.room_call_name("pilot") == "#pilot call"
    assert cn.room_call_name("#pilot") == "#pilot call"
    assert cn.person_call_name("") == "Call" and cn.room_call_name("") == "Room call"


def test_the_topic_renames_it_and_keeps_who_or_where():
    assert cn.renamed("#pilot call", "ADFS test window") == "#pilot · ADFS test window"
    assert cn.renamed("Call with Megan", "ADFS test window") == "Call with Megan · ADFS test window"
    assert cn.renamed("#pilot call", "") == "#pilot call"


def test_a_topic_is_a_few_plain_words_never_filler_ids_or_dates():
    assert cn.clean_topic('"adfs test window."') == "Adfs test window"
    assert cn.clean_topic("one two three four five six seven") == "One two three four five"
    for bad in ("", "Meeting", "call", "2026-10-18 sync", "see /docs/x", "call-abcd1234 review"):
        assert cn.clean_topic(bad) == "", bad


class _Store:
    def __init__(self, room=None, title="", humans=()):
        self._room, self._title, self._humans = room, title, list(humans)

    def room(self, _sid):
        return self._room

    def humans(self, _sid):
        return self._humans

    def get(self, _sid):
        return {"title": self._title}


def test_base_for_a_room_of_two_names_the_other_person_and_records_both():
    s = _Store(room={"workspace": "pair-x"}, humans=["alice", "megan"])
    name, meta = cn.base_for(s, "s-1", "alice", is_pair=lambda ws: ws == "pair-x",
                             name_of={"alice": "Alice Lee", "megan": "Megan Ross"}.get)
    assert name == "Call with Megan"
    assert meta[cn.KIND_KEY] == "person"
    assert meta[cn.WITH_KEY] == [{"subject": "alice", "name": "Alice Lee"}, {"subject": "megan", "name": "Megan Ross"}]


def test_base_for_a_named_room_and_none_for_a_private_chat():
    s = _Store(room={"workspace": "team"}, title="pilot")
    assert cn.base_for(s, "s-1", "alice", is_pair=lambda _ws: False, name_of=lambda _x: "")[0] == "#pilot call"
    assert cn.base_for(_Store(room=None), "s-1", "alice", is_pair=lambda _ws: False, name_of=lambda _x: "") is None
    # a private chat's call is named after the chat when it has a real name; never a placeholder or an id
    assert cn.base_for(_Store(room=None, title="Thinking out loud"), "s-1", "alice", is_pair=lambda _ws: False,
                       name_of=lambda _x: "")[0] == "Thinking out loud"
    for t in ("Meeting", "New chat", "pchat-abcd1234", "s-0123456789ab"):
        assert cn.base_for(_Store(room=None, title=t), "s-1", "alice", is_pair=lambda _ws: False, name_of=lambda _x: "") is None


# ── the topic pass ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh():
    cn.reset()
    yield
    cn.reset()


def _row(title, named, renamed_from=None):
    meta = {cn.NAMED_KEY: named}
    if renamed_from:
        meta[cn.RENAMED_FROM_KEY] = renamed_from
    return {"data": {"title": title, "metadata": meta}}


LONG = "Alice: we need the ADFS test window agreed with the bank this week. " * 6


def _pass(row, transcript, topic="ADFS test window", now=1000.0):
    asked, wrote = [], []

    def complete(system, user):
        asked.append(user)
        return {"topic": topic}

    picked = cn.rename_pass("alice", [{"meeting_id": 7, "session_id": "s-1"}], row_of=lambda _m: row,
                            transcript_of=lambda _m: transcript, complete=complete,
                            titler=lambda *a: wrote.append(a) or True, now=now, run=lambda f: f())
    return picked, asked, wrote


def test_a_live_call_is_renamed_once_by_its_topic_and_says_what_it_was():
    picked, asked, wrote = _pass(_row("#pilot call", "#pilot call"), LONG)
    assert picked == ["7"] and len(asked) == 1
    assert wrote == [("alice", 7, "#pilot · ADFS test window", {cn.RENAMED_FROM_KEY: "#pilot call"})]
    # once: a later pass does not ask again
    assert _pass(_row("#pilot · ADFS test window", "#pilot call", "#pilot call"), LONG, now=2000.0)[0] == []


def test_a_name_the_person_typed_is_never_replaced():
    picked, asked, wrote = _pass(_row("Budget review", "#pilot call"), LONG)
    assert asked == [] and wrote == []


def test_no_model_call_before_the_call_has_anything_to_name():
    picked, asked, wrote = _pass(_row("#pilot call", "#pilot call"), "Alice: hi")
    assert asked == [] and wrote == []
    # …and it is tried again on a later pass
    assert _pass(_row("#pilot call", "#pilot call"), LONG, now=1100.0)[2]


def test_a_model_that_finds_no_topic_is_asked_at_most_three_times():
    for i in range(5):
        _pass(_row("#pilot call", "#pilot call"), LONG, topic="", now=1000.0 + i * 100)
    assert cn._attempts["7"] == cn.MAX_ATTEMPTS


def test_no_model_or_no_titler_means_no_pass():
    assert cn.rename_pass("alice", [{"meeting_id": 7}], row_of=lambda _m: None, transcript_of=lambda _m: LONG,
                          complete=None, titler=lambda *a: True, run=lambda f: f()) == []


# ── where a call starts, it is named ────────────────────────────────────────────────────────────

def test_call_person_names_the_call_after_the_person(w, monkeypatch):
    named = []
    monkeypatch.setattr(cn, "name_call", lambda titler, owner, mid, base: named.append((owner, mid, base[0])) or True)
    r = _call(w, {"person": "tester-b"}, **AGENT)
    assert r.status_code == 200, r.text
    assert named == [("alice", r.json()["call"]["meeting_id"], "Call with tester-b")]


def test_fresh_ends_only_your_own_empty_call_and_starts_another(w):
    first = _call(w, {"person": "tester-b"}).json()
    sid, room1 = first["session_id"], first["call"]["room"]
    # asked again without `fresh`: the live call is rejoined, not duplicated
    again = _call(w, {"person": "tester-b"}).json()
    assert again["already_live"] is True and again["call"]["room"] == room1 and len(w.meetings.calls) == 1
    # "Start a new one": nobody is in the old call, so it ends and a new one starts
    new = _call(w, {"person": "tester-b", "fresh": True}).json()
    assert new["already_live"] is False and new["call"]["room"] != room1 and len(w.meetings.calls) == 2
    assert w.store.active_call(sid)["room"] == new["call"]["room"]


def test_fresh_never_ends_a_call_someone_else_is_in(w):
    first = _call(w, {"person": "tester-b"}).json()
    room1 = first["call"]["room"]
    _Rooms.rooms[room1] = {**_Rooms.rooms[room1], "num_participants": 2, "humans": 1,
                           "in_now": [{"identity": "tester-b", "name": "Tester B"}]}
    r = _call(w, {"person": "tester-b", "fresh": True})
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "still_in_call"
    assert "Tester B" in r.json()["detail"]["said"] and len(w.meetings.calls) == 1


def test_an_agent_cannot_end_a_call_with_fresh(w):
    first = _call(w, {"person": "tester-b"}).json()
    r = _call(w, {"person": "tester-b", "fresh": True}, **AGENT)
    assert r.status_code == 200 and r.json()["already_live"] is True
    assert r.json()["call"]["room"] == first["call"]["room"] and len(w.meetings.calls) == 1


# ── who is in the call now ──────────────────────────────────────────────────────────────────────

def test_the_live_call_says_who_is_in_it_never_the_listener(w):
    first = _call(w, {"person": "tester-b"}).json()
    sid, room1 = first["session_id"], first["call"]["room"]
    _Rooms.rooms[room1] = {**_Rooms.rooms[room1], "num_participants": 3, "humans": 2,
                           "in_now": [{"identity": "alice", "name": "Alice"}, {"identity": "tester-b", "name": "Tester B"}]}
    r = w.c.get(f"/api/sessions/{sid}/call", headers={"X-User-Id": "alice", "x-user-email": "alice@acme.test",
                                                        "x-user-credential-class": "session"})
    assert r.status_code == 200, r.text
    assert r.json()["call"]["in_now"] == [{"subject": "alice", "name": "Alice"}, {"subject": "tester-b", "name": "Tester B"}]
