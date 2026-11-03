"""Deny vectors and rules: the owner shares a chat WITH its history (ADR-0048 decision 4).

- The owner chooses: ``history: all`` (the default) shares the chat in place (the person joins it,
  history and all); ``from:<turn>`` starts a new shared chat carrying the visible text from that turn
  on; ``none`` starts a new empty one. Naming what is sensitive first is the agent's job in chat.
- Someone who is not the owner adding a person gets a new empty chat: they cannot disclose the
  owner's history. A viewer adds nobody.
- Sharing a chat grants no workspace.
"""
from __future__ import annotations

import json

from control_plane import session_sync
from tests.test_access_sessions_share import (SECRET_TOOL_INPUT, SECRET_TOOL_RESULT, _add, _h,  # noqa: F401
                                              env)


def test_the_owner_sharing_with_no_choice_shares_in_place(env):
    """'share this chat with her' means this chat: no history named is `all`."""
    client, store, private = env
    r = _add(client)
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == private
    assert [p["principal"] for p in store.participants(private)] == ["alice", "bob"]
    assert client.get(f"/api/sessions/{private}/history", headers=_h("bob")).status_code == 200


def test_the_owner_shares_in_place_with_history(env):
    client, store, private = env
    r = _add(client, history="all")
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == private
    assert [p["principal"] for p in store.participants(private)] == ["alice", "bob"]
    h = client.get(f"/api/sessions/{private}/history", headers=_h("bob"))
    assert h.status_code == 200
    assert [t["role"] for t in h.json()["turns"]] == ["user", "agent", "user", "agent"]
    assert session_sync.bundle_for(store, "bob", private)["version"] >= 1
    rail = client.get("/api/sessions", headers=_h("bob")).json()["sessions"]
    assert private in [x["session"] for x in rail]


def test_the_owner_shares_from_a_turn(env):
    client, store, private = env
    r = _add(client, history="from:2")
    assert r.status_code == 200, r.text
    shared = r.json()["session_id"]
    assert shared != private
    msgs = [m["text"] for m in client.get(f"/api/sessions/{shared}/messages", headers=_h("bob")).json()["messages"]]
    assert msgs == ["(excerpt) me: thanks", "(excerpt) agent: Any time."]
    blob = json.dumps(client.get(f"/api/sessions/{shared}/history", headers=_h("bob")).json())
    for bit in (SECRET_TOOL_INPUT, SECRET_TOOL_RESULT, "summarise my inbox"):
        assert bit not in blob
    assert client.get(f"/api/sessions/{private}/history", headers=_h("bob")).status_code == 404


def test_from_a_turn_that_does_not_exist_is_refused(env):
    client, store, private = env
    assert _add(client, history="from:9").status_code == 422
    assert store.list_for("bob") == []


def test_the_owner_shares_with_no_history(env):
    client, store, private = env
    r = _add(client, history="none")
    shared = r.json()["session_id"]
    assert r.status_code == 200 and shared != private
    assert store.load_bundle(shared) == {"version": 0, "files": {}}


def test_a_non_owner_adding_a_person_gets_a_new_empty_chat(env):
    client, store, private = env
    _add(client, history="all")                                   # bob is a member of alice's chat
    r = client.post(f"/api/sessions/{private}/participants", json={"email": "carol@acme.test"},
                    headers=_h("bob"))
    assert r.status_code == 200, r.text
    new = r.json()["session_id"]
    assert new != private and r.json()["owner"] == "bob"
    assert [p["principal"] for p in store.participants(private)] == ["alice", "bob"]
    assert client.get(f"/api/sessions/{private}/history", headers=_h("carol")).status_code == 404
    assert store.load_bundle(new)["files"] == {}


def test_a_non_owner_cannot_choose_the_owners_history(env):
    client, store, private = env
    _add(client, history="all")
    for h in ("all", "from:0"):
        r = client.post(f"/api/sessions/{private}/participants",
                        json={"email": "carol@acme.test", "history": h}, headers=_h("bob"))
        assert r.status_code == 403 and r.json()["detail"] == "history_is_the_owners"
    assert client.get(f"/api/sessions/{private}/history", headers=_h("carol")).status_code == 404


def test_a_viewer_adds_nobody(env):
    client, store, private = env
    _add(client, history="all", role="viewer")
    r = client.post(f"/api/sessions/{private}/participants", json={"email": "carol@acme.test"},
                    headers=_h("bob"))
    assert r.status_code == 403


def test_sharing_a_chat_grants_no_workspace(env):
    """Joining shares the conversation and nothing else: the newcomer is a member of no workspace
    of the owner's, so a turn here by either person is held to what both can read."""
    from control_plane import session_access
    client, store, private = env
    members = {"deal-room": {"alice"}}                      # alice's workspace; bob is not in it
    _add(client, history="all")
    others = [h for h in store.humans(private) if h != "alice"]
    assert others == ["bob"]
    scope = session_access.shared_scope("alice", others=others,
                                        candidates=["alice", "_system", "_global", "deal-room"],
                                        is_member=lambda ws, who: who in members.get(ws, set()))
    assert scope.allowed == ["_global"]
    assert set(scope.withheld) == {"alice", "_system", "deal-room"}
    assert members["deal-room"] == {"alice"}                 # sharing the chat added bob nowhere


def test_the_share_preview_names_the_choice_and_what_the_history_read(env):
    client, store, private = env
    pv = client.get("/api/sessions/pchat-1/share-preview", headers=_h("alice")).json()
    assert pv["forks"] is True and pv["history"]["default"] == "all"
    assert pv["history"]["options"] == ["all", "from:<turn>", "none"] and pv["history"]["turns"] == 4
    assert pv["private_reads"] == ["Gmail"]


def test_the_mention_guidance_asks_the_owner_before_sharing_the_history():
    from control_plane import people_directory
    text = people_directory.mention_block([{"subject": "u_e", "name": "Eugenia"}], ["u_e"])
    assert "history" in text and "sensitive" in text and "owner's yes" in text
