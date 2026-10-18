"""A draft never reads as sent mail, a thread's drafts carry their draft ids, and replacing a draft the
person wrote is an explicit, attended act.

Friction (2026-10-16): gmail_search/gmail_thread returned drafts with no DRAFT label, so the agent told
the person a recap "went out" when it was a draft; and gmail_draft_update refused every draft Vexa had
not created, with no way to find a thread's draft, so stale drafts piled up.
"""
import base64
import time
from unittest.mock import patch

import httpx
import pytest

from conftest import GATEWAY_KEY, OWNER
from credential_broker import identity_token, providers

COMPOSE = {"access_token": "TOKEN", "expires_at": time.time() + 500,
           "scope": providers.DRAFT_SCOPE + " " + providers.READ_SCOPE}


def _text(s: bytes) -> dict:
    return {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(s).decode()}, "headers": []}


def test_search_and_read_carry_labels_and_is_draft():
    def respond(req):
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "sent1"}, {"id": "draft1"}]})
        mid = req.url.path.rsplit("/", 1)[-1]
        labels = ["DRAFT"] if mid == "draft1" else ["SENT", "INBOX"]
        return httpx.Response(200, json={"id": mid, "threadId": "t1", "labelIds": labels, "payload": _text(b"x")})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        found = providers.read_account("google_email", {"access_token": "x"}, "gmail.search", query="recap", http=c)
        one = providers.read_account("google_email", {"access_token": "x"}, "gmail.read", message_id="draft1", http=c)
    by_id = {m["id"]: m for m in found["messages"]}
    assert by_id["draft1"]["is_draft"] is True and by_id["draft1"]["labels"] == ["DRAFT"]
    assert by_id["sent1"]["is_draft"] is False and set(by_id["sent1"]["labels"]) == {"SENT", "INBOX"}
    assert one["message"]["is_draft"] is True


def test_a_thread_returns_its_drafts_with_their_draft_ids():
    calls = []

    def respond(req):
        calls.append(req)
        if "/threads/" in req.url.path:
            return httpx.Response(200, json={"id": "t1", "messages": [
                {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "payload": _text(b"hello")},
                {"id": "m2", "threadId": "t1", "labelIds": ["DRAFT"], "payload": _text(b"recap draft")}]})
        assert req.url.path.endswith("/drafts") and req.method == "GET"
        return httpx.Response(200, json={"drafts": [{"id": "r-other", "message": {"id": "zz", "threadId": "t9"}},
                                                    {"id": "r-recap", "message": {"id": "m2", "threadId": "t1"}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        out = providers.read_account("google_email", {"access_token": "x"}, "gmail.thread", message_id="t1", http=c)
    first, second = out["messages"]
    assert first["is_draft"] is False and "draft_id" not in first
    assert second["is_draft"] is True and second["draft_id"] == "r-recap"
    assert out["drafts"] == [{"draft_id": "r-recap", "message_id": "m2"}]


def test_a_thread_without_drafts_makes_no_drafts_call():
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={"id": "t1", "messages": [
            {"id": "m1", "threadId": "t1", "labelIds": ["SENT"], "payload": _text(b"x")}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        out = providers.read_account("google_email", {"access_token": "x"}, "gmail.thread", message_id="t1", http=c)
    assert len(calls) == 1 and out["drafts"] == []


@pytest.fixture
def mailbox(connection, ready, store):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {**COMPOSE, "expires_at": time.time() + 500}})
    ready(cid)
    return cid


UPDATE = {"request_id": "fixture-person-1", "recipient": "ada@example.test", "subject": "s", "body": "b",
          "draft_id": "r-person-wrote"}
UPDATED = {"draft_id": "r-person-wrote", "message_id": "m1", "status": "draft_updated", "sent": False}


def test_a_person_draft_is_refused_by_default_with_what_to_do(signed, mailbox):
    with patch.object(providers, "create_gmail_draft", return_value=UPDATED) as put:
        r = signed("agent", "POST", f"/api/connections/{mailbox}/draft-update", UPDATE)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Only drafts Vexa created" in detail and "replace_person_draft" in detail
    put.assert_not_called()


def test_the_person_may_have_their_own_draft_replaced_in_an_attended_chat(signed, mailbox, broker):
    with patch.object(providers, "create_gmail_draft", return_value=UPDATED) as put:
        r = signed("agent", "POST", f"/api/connections/{mailbox}/draft-update", {**UPDATE, "replace_person_draft": True})
    assert r.status_code == 200 and r.json()["status"] == "draft_updated"
    assert put.call_args.kwargs["draft_id"] == "r-person-wrote"
    outcomes = [row["outcome"] for row in broker.sql("SELECT outcome FROM audit WHERE action='gmail.draft.update'", rows=True)]
    assert "person_draft_replace_requested" in outcomes


def test_replacing_a_person_draft_is_never_unattended(signed, mailbox):
    worker = identity_token.sign(GATEWAY_KEY, {"sub": "product-user", "scopes": ["bot", "tx"],
                                               "delegation": {"regime": "autonomous", "workspaces": "*"}})
    with patch.object(providers, "create_gmail_draft", return_value=UPDATED) as put:
        r = signed("agent", "POST", f"/api/connections/{mailbox}/draft-update",
                   {**UPDATE, "replace_person_draft": True}, identity=worker)
    assert r.status_code == 403
    put.assert_not_called()
