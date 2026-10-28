"""A room of two is called by the other person, wherever it is referenced (UI 7ii, humans by name).

Live app.dev 2026-10-18: `call_person` answered with a `[[room:s-…]]` chip for the room it opened
with tester-b, and the chip read "Untitled chat" — the resolver labelled a room with its stored
title, and a room of two has none. The label is made on the server, in the one resolver every
surface asks (a chip in a chat, a page, a pasted URL, the `ref_resolve` tool), so it is fixed there:
each of the two people sees the OTHER one's name, the name the chat list gives the same room.

L2, in-process (the fixture of `test_chat_list_reads`). Synthetic people only.
"""
from __future__ import annotations

from tests.test_chat_list_reads import w  # noqa: F401 — the fixture
from tests.test_s4_rooms import H


def _resolve(w, who: str, ref: str) -> dict:
    r = w.c.get("/api/refs/resolve", params={"ref": ref}, headers=H(who))
    assert r.status_code == 200, r.text
    return r.json()


def _direct(w, opener: str, other: str) -> str:
    r = w.c.post("/api/rooms/direct", json={"subject": other}, headers=H(opener))
    assert r.status_code in (200, 201), r.text
    return r.json()["session_id"]


def test_each_of_the_two_sees_the_other_persons_name_on_the_room_chip(w):  # noqa: F811
    sid = _direct(w, "alice", "bob")
    for viewer, other in (("alice", "bob"), ("bob", "alice")):
        got = _resolve(w, viewer, f"[[room:{sid}]]")
        assert got["access"] == "readable" and got["kind"] == "room"
        assert got["label"] == other, got                       # not "Untitled chat", not an id
        # the same name the viewer's chat list gives that room
        rows = [r for r in w.c.get("/api/sessions", headers=H(viewer)).json()["sessions"]
                if r.get("session_id") == sid]
        assert [r["other_name"] for r in rows] == [got["label"]]


def test_a_chat_reference_to_the_same_room_is_named_the_same_way(w):  # noqa: F811
    sid = _direct(w, "alice", "dan")
    assert _resolve(w, "alice", f"[[chat:{sid}]]")["label"] == "dan"
    assert _resolve(w, "alice", f"/r/room/{sid}")["label"] == "dan"


def test_the_writers_label_is_still_not_echoed_and_a_stranger_learns_no_name(w):  # noqa: F811
    sid = _direct(w, "alice", "bob")
    got = _resolve(w, "alice", f"[[room:{sid}|Secret plans]]")
    assert got["label"] == "bob"
    stranger = _resolve(w, "dan", f"[[room:{sid}]]")
    assert stranger["access"] == "not-yours" and stranger["label"] is None


def test_a_room_that_is_not_a_pair_keeps_its_own_title(w):  # noqa: F811
    r = w.c.post("/api/rooms", json={"workspace": "team", "name": "Launch", "visibility": "open", "members": []},
                 headers=H("alice"))
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    label = _resolve(w, "alice", f"[[room:{sid}]]")["label"]
    assert label == w.store.get(sid)["title"] and label not in ("bob", "dan")
