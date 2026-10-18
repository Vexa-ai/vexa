"""S5 (PRD 0.13.4 H3, deny test 13): a 7g inline card is a pending item. A press is bound to the item
by a server nonce and the content hash of the card the AGENT wrote, decided once, by the person, from
the session credential — never replayed, never by a delegated caller, never on a changed card."""
from __future__ import annotations

import json

from tests.test_r1_rooms import _h, _key, world  # noqa: F401  (fixture)

RECORDS = ".claude/sessions/conversation.turns.jsonl"
ASK = {"id": "new-ws", "question": "Create the workspace “Family Health”?",
       "primary": {"label": "Create"}, "secondary": {"label": "Not now"}}


def _chat(store, owner="alice", ask=ASK, extra_turns=()):
    row = store.create(owner, name="c-1")
    recs = [{"user_text": "make me a family health workspace", "ts": 1.0,
             "reply": "Sure.\n\n```ask\n" + json.dumps(ask) + "\n```\n"}, *extra_turns]
    store.save_bundle(row["id"], 0, {RECORDS: "\n".join(json.dumps(r) for r in recs).encode()})
    return row["id"]


def _open(client, sid, who="alice", ask_id="new-ws"):
    return client.post("/api/asks/open", json={"session": sid, "ask_id": ask_id}, headers=_h(who))


def test_a_press_answers_the_pending_item_once(world):
    client, store, *_ = world
    sid = _chat(store)
    o = _open(client, sid)
    assert o.status_code == 200, o.text
    item = o.json()
    assert item["status"] == "open" and item["spec"]["id"] == "new-ws" and len(item["content_hash"]) == 64
    body = {"nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": "Create"}}
    a = client.post(f"/api/asks/{item['pending_id']}/answer", json=body, headers=_h("alice"))
    assert a.status_code == 200, a.text
    assert a.json()["message"] == f"[answer new-ws #{item['pending_id']}] Create"
    replay = client.post(f"/api/asks/{item['pending_id']}/answer", json=body, headers=_h("alice"))
    assert replay.status_code == 409                                  # not replayable
    again = _open(client, sid).json()
    assert again["status"] == "answered" and again["answer"] == "Create"


def test_the_answer_is_bound_to_its_item_and_to_the_card_the_agent_wrote(world):
    client, store, *_ = world
    sid = _chat(store)
    item = _open(client, sid).json()
    url = f"/api/asks/{item['pending_id']}/answer"
    wrong_nonce = {"nonce": "x" * 32, "content_hash": item["content_hash"], "reply": {"label": "Create"}}
    assert client.post(url, json=wrong_nonce, headers=_h("alice")).status_code == 409
    wrong_face = {"nonce": item["nonce"], "content_hash": "0" * 64, "reply": {"label": "Create"}}
    assert client.post(url, json=wrong_face, headers=_h("alice")).status_code == 409
    not_offered = {"nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": "Delete all"}}
    assert client.post(url, json=not_offered, headers=_h("alice")).status_code == 422
    assert _open(client, sid, ask_id="no-such").status_code == 404     # only cards the agent posed


def test_a_card_the_person_typed_is_not_an_agent_ask(world):
    client, store, *_ = world
    row = store.create("alice", name="c-2")
    rec = {"user_text": "```ask\n" + json.dumps(ASK) + "\n```", "ts": 1.0, "reply": "ok"}
    store.save_bundle(row["id"], 0, {RECORDS: json.dumps(rec).encode()})
    assert _open(client, row["id"]).status_code == 404


def test_refused_to_a_delegated_caller_an_api_key_and_another_person(world):
    client, store, *_ = world
    sid = _chat(store)
    item = _open(client, sid).json()
    body = {"nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": "Create"}}
    url = f"/api/asks/{item['pending_id']}/answer"
    watched = {"X-User-Id": "alice", "x-user-regime": "human", "x-user-delegation-workspaces": "*",
               "x-user-credential-class": "delegation"}
    assert client.post(url, json=body, headers=watched).status_code == 403
    assert client.post(url, json=body, headers=_key("alice")).status_code == 403
    assert client.post(url, json=body, headers=_h("bob")).status_code == 404
    assert _open(client, sid, who="bob").status_code == 404
    assert client.post("/api/asks/open", json={"session": sid, "ask_id": "new-ws"}, headers=watched).status_code == 403
