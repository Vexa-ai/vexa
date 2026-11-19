"""DENY TEST (gate Low 8): a worker's ``replace_person_draft`` replaces the person's own Gmail draft only
on the person's press.

The flag used to be the model's word that "the person asked in this chat". Now an attended worker's
call carries ``pressed``: the person's answered ```ask card in THIS chat, naming the draft and the new recipient,
pressed on its primary button, spent once. The person's own app needs no press.

Offline: FastAPI over a sqlite session store, the broker replaced by a recorder. No network.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import route_policy
from control_plane.identity_token import DELEGATION_HEADERS
from control_plane.routers import asks, connections
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs

SIGNED = "v1.signed-by-the-gateway.for-u1"
READY = [{"id": "a" * 32, "provider": "google_email", "status": "ready", "label": "Gmail"}]
BODY = {"request_id": "req-00000001", "recipient": "ada@example.test", "subject": "s", "body": "b",
        "draft_id": "r-person-wrote", "replace_person_draft": True}
APP = {"X-User-Id": "u1", "X-Vexa-Identity": SIGNED, "x-user-credential-class": "session"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    calls: list = []

    def fake(actor, method, path, payload=None, *, identity):
        calls.append((method, path, payload))
        if path == "/api/connections":
            return {"connections": READY}
        if path == "/api/preferences":
            return {}
        return {"status": "draft_updated", "sent": payload}

    monkeypatch.setattr(connections, "call_broker", fake)
    glob = tmp_path / "_global"
    glob.mkdir()
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    subject_of = lambda r: r.headers["x-user-id"]  # noqa: E731
    app = FastAPI(dependencies=[route_policy.PERSON_GATE])
    app.include_router(connections.build(subject_of=subject_of, wsr=object(), _global_root=lambda: glob,
                                         _read_target=lambda request, slug: tmp_path, session_store=store))
    app.include_router(asks.build(session_store=store, subject_of=subject_of))
    chat = store.create("u1", name="pchat")["id"]
    yield TestClient(app), store, chat, calls
    store.close()


def _worker(chat: str) -> dict:
    """The attended agent of the person's chat: the delegation headers the identity door stamps."""
    return {"X-User-Id": "u1", "X-Vexa-Identity": SIGNED, DELEGATION_HEADERS["regime"]: "human",
            DELEGATION_HEADERS["workspaces"]: "", DELEGATION_HEADERS["unit"]: f"agent-u1-chat-{chat}-g1"}


def _card(store, chat: str, question: str, ask_id: str = "replace-draft") -> None:
    card = {"id": ask_id, "question": question, "primary": {"label": "Replace my draft"},
            "secondary": {"label": "Keep it"}}
    store.save_bundle(chat, store.head(chat), {".claude/sessions/conversation.turns.jsonl": json.dumps(
        {"user_text": "replace my draft", "ts": 1.0, "reply": "```ask\n" + json.dumps(card) + "\n```\n"}).encode()})


def _press(client, chat: str, label: str = "Replace my draft", ask_id: str = "replace-draft") -> str:
    item = client.post("/api/asks/open", json={"session": chat, "ask_id": ask_id}, headers=APP).json()
    r = client.post(f"/api/asks/{item['pending_id']}/answer", headers=APP, json={
        "nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": label}})
    assert r.status_code == 200, r.text
    return item["pending_id"]


def _forwarded(calls) -> list:
    return [c for c in calls if c[1].endswith("/draft-update")]


def test_a_worker_cannot_replace_the_persons_own_draft_on_its_own_word(world):
    client, _store, chat, calls = world
    r = client.post("/api/connections/gmail/draft/update", json=BODY, headers=_worker(chat))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "press_required", r.text
    assert _forwarded(calls) == []


def test_a_press_on_another_card_or_another_button_replaces_nothing(world):
    client, store, chat, calls = world
    _card(store, chat, "Replace your draft to bob@example.test?")            # names someone else
    pending = _press(client, chat)
    r = client.post("/api/connections/gmail/draft/update", json={**BODY, "pressed": pending}, headers=_worker(chat))
    assert r.status_code == 403
    _card(store, chat, "Replace your draft to ada@example.test?", ask_id="replace-2")
    pending = _press(client, chat, label="Keep it", ask_id="replace-2")      # the secondary button
    r = client.post("/api/connections/gmail/draft/update", json={**BODY, "pressed": pending}, headers=_worker(chat))
    assert r.status_code == 403 and _forwarded(calls) == []


def test_a_press_replaces_only_the_draft_its_card_names(world):
    """The press is bound to the draft the person reviewed, not only to its recipient: a card naming
    one draft does not replace another draft to the same recipient, and a card naming no draft
    replaces none."""
    client, store, chat, calls = world
    _card(store, chat, "Replace your own draft r-person-wrote with the new text to ada@example.test?")
    pending = _press(client, chat)
    r = client.post("/api/connections/gmail/draft/update",
                    json={**BODY, "draft_id": "r-another-draft", "pressed": pending}, headers=_worker(chat))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "press_required", r.text
    _card(store, chat, "Replace your own draft to ada@example.test?", ask_id="replace-2")
    pending = _press(client, chat, ask_id="replace-2")
    r = client.post("/api/connections/gmail/draft/update", json={**BODY, "pressed": pending}, headers=_worker(chat))
    assert r.status_code == 403 and _forwarded(calls) == []


def test_the_persons_press_replaces_their_draft_once(world):
    """Positive control: the press naming the draft and the recipient lets the worker replace it, once."""
    client, store, chat, calls = world
    _card(store, chat, "Replace your own draft r-person-wrote with the new text to ada@example.test?")
    pending = _press(client, chat)
    r = client.post("/api/connections/gmail/draft/update", json={**BODY, "pressed": pending}, headers=_worker(chat))
    assert r.status_code == 200, r.text
    sent = _forwarded(calls)[-1][2]
    assert sent["replace_person_draft"] is True and "pressed" not in sent
    again = client.post("/api/connections/gmail/draft/update",
                        json={**BODY, "request_id": "req-00000002", "pressed": pending}, headers=_worker(chat))
    assert again.status_code == 403 and len(_forwarded(calls)) == 1


def test_the_persons_own_app_needs_no_press(world):
    client, _store, _chat, calls = world
    r = client.post("/api/connections/gmail/draft/update", json=BODY,
                    headers={"X-User-Id": "u1", "X-Vexa-Identity": SIGNED})
    assert r.status_code == 200 and _forwarded(calls)[-1][2]["replace_person_draft"] is True
