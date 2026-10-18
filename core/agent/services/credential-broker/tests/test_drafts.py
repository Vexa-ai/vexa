"""Gmail drafts: permission-gated, idempotent by request id, never a send."""
import base64
import json
import time
from unittest.mock import patch

import httpx
import pytest

from credential_broker import providers
from conftest import OWNER


def test_permission_required_then_idempotent_creation(signed, connection, ready, store):
    cid = connection("google_email")
    value = {"access_token": "TOKEN", "expires_at": time.time() + 500, "scope": ""}
    store.put(cid, {"owner": OWNER, "value": value})
    ready(cid)
    payload = {"request_id": "fixture-draft-" + cid[:8], "recipient": "test@example.test", "subject": "fixture", "body": "private draft"}
    path = f"/api/connections/{cid}/draft"
    with patch.object(providers, "create_gmail_draft", return_value={"draft_id": "draft", "message_id": None,
                                                                     "status": "draft_created", "sent": False}) as create:
        assert signed("agent", "POST", path, payload).json()["status"] == "permission_required"
        create.assert_not_called()
        store.put(cid, {"owner": OWNER, "value": {**value, "scope": providers.DRAFT_SCOPE}})
        ready(cid, version=2)
        for _ in range(2):
            assert signed("agent", "POST", path, payload).json()["status"] == "draft_created"
        assert create.call_count == 1
        assert signed("agent", "POST", path, {**payload, "body": "changed"}).status_code == 409
        assert signed("agent", "POST", path, payload, actor="other").status_code == 404


def test_provider_draft_only_and_header_injection_refused():
    def respond(req):
        assert str(req.url) == "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
        decoded = base64.urlsafe_b64decode(json.loads(req.content)["message"]["raw"]).decode()
        assert "Subject: fixture" in decoded
        return httpx.Response(200, json={"id": "draft", "message": {"id": "msg"}, "access_token": "hidden"})
    value = {"access_token": "token", "scope": providers.DRAFT_SCOPE}
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        assert providers.create_gmail_draft(value, "test@example.test", "fixture", "body", http=c)["sent"] is False
        with pytest.raises(providers.ProviderError):
            providers.create_gmail_draft(value, "test@example.test", "x\r\nBcc: other@example.test", "body", http=c)


# ── richer drafts: reply in a thread, update, cc/bcc, attachments, policy ───────────────────────

COMPOSE = {"access_token": "TOKEN", "expires_at": time.time() + 500,
           "scope": providers.DRAFT_SCOPE + " " + providers.READ_SCOPE}
CREATED = {"draft_id": "r-draft-1", "message_id": "m1", "status": "draft_created", "sent": False}


@pytest.fixture
def mailbox(connection, ready, store):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": COMPOSE})
    ready(cid)
    return cid


def _decoded(req):
    return base64.urlsafe_b64decode(json.loads(req.content)["message"]["raw"]).decode()


def gmail(seen, thread="t1"):
    original = {"id": "m0", "threadId": thread, "payload": {"headers": [
        {"name": "Message-ID", "value": "<orig@mail.example.test>"},
        {"name": "References", "value": "<first@mail.example.test>"},
        {"name": "Subject", "value": "Quarterly plan"}]}}

    def respond(req):
        seen.append(req)
        if req.method == "GET" and "/threads/" in req.url.path:
            return httpx.Response(200, json={"id": thread, "messages": [{"id": "older", "threadId": thread,
                                                                          "payload": {"headers": []}}, original]})
        if req.method == "GET":
            return httpx.Response(200, json=original) if req.url.path.endswith("/m0") else httpx.Response(404, json={})
        return httpx.Response(200, json={"id": "r-draft-1", "message": {"id": "m9", "threadId": thread}})
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_a_reply_draft_lands_in_the_thread_with_correct_headers():
    seen = []
    with gmail(seen) as c:
        out = providers.create_gmail_draft(COMPOSE, "ada@example.test", "", "Thanks", thread_id="t1",
                                           cc=["bo@example.test"], bcc=["cy@example.test"], http=c)
    post = seen[-1]
    assert post.method == "POST" and post.url.path.endswith("/drafts")
    assert json.loads(post.content)["message"]["threadId"] == "t1"
    raw = _decoded(post)
    assert "In-Reply-To: <orig@mail.example.test>" in raw
    assert "References: <first@mail.example.test> <orig@mail.example.test>" in raw
    assert "Subject: Re: Quarterly plan" in raw
    assert "Cc: bo@example.test" in raw and "Bcc: cy@example.test" in raw
    assert out["thread_id"] == "t1" and out["sent"] is False


@pytest.mark.parametrize("subject", ["", "   "])
def test_a_reply_with_an_empty_subject_goes_through_the_route_as_re_original(signed, mailbox, subject):
    """Route to provider, end to end: the agent route forwards "" for an omitted subject, and the
    broker answers with a draft titled "Re: <original>" rather than a 422 (#1783)."""
    seen, real = [], providers.create_gmail_draft

    def through_mock_gmail(*args, **kwargs):
        with gmail(seen) as c:
            return real(*args, http=c, **kwargs)
    payload = {"request_id": "fixture-draft-30", "recipient": "ada@example.test", "subject": subject,
               "body": "Thanks", "thread_id": "t1"}
    with patch.object(providers, "create_gmail_draft", side_effect=through_mock_gmail):
        r = signed("agent", "POST", f"/api/connections/{mailbox}/draft", payload)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft_created"
    assert "Subject: Re: Quarterly plan" in _decoded(seen[-1])


def test_a_named_message_outside_the_thread_is_refused():
    with gmail([], thread="other") as c:
        with pytest.raises(providers.ProviderError, match="not in this thread"):
            providers.create_gmail_draft(COMPOSE, "ada@example.test", "", "x", thread_id="t1", in_reply_to="m0", http=c)


def test_an_update_puts_the_named_draft_and_never_sends():
    seen = []
    with gmail(seen) as c:
        out = providers.create_gmail_draft(COMPOSE, "ada@example.test", "s", "b", draft_id="r-draft-1", http=c)
    assert seen[-1].method == "PUT" and seen[-1].url.path.endswith("/drafts/r-draft-1")
    assert all("/send" not in r.url.path for r in seen)
    assert out["status"] == "draft_updated"


def test_attachments_are_attached():
    seen = []
    with gmail(seen) as c:
        providers.create_gmail_draft(COMPOSE, "ada@example.test", "s", "b", http=c,
                                     attachments=[("agenda.md", "text/markdown", b"# Agenda\n")])
    raw = _decoded(seen[-1])
    assert 'filename="agenda.md"' in raw and "Content-Type: text/markdown" in raw


def test_only_drafts_this_broker_created_can_be_updated(signed, mailbox, connection, ready, store):
    payload = {"request_id": "fixture-draft-10", "recipient": "ada@example.test", "subject": "s", "body": "b"}
    with patch.object(providers, "create_gmail_draft", return_value=CREATED):
        assert signed("agent", "POST", f"/api/connections/{mailbox}/draft", payload).json()["draft_id"] == "r-draft-1"
    update = {**payload, "request_id": "fixture-draft-11", "draft_id": "r-draft-1"}
    with patch.object(providers, "create_gmail_draft", return_value={**CREATED, "status": "draft_updated"}) as put:
        assert signed("agent", "POST", f"/api/connections/{mailbox}/draft-update", update).json()["status"] == "draft_updated"
        assert put.call_args.kwargs["draft_id"] == "r-draft-1"
        foreign = {**update, "request_id": "fixture-draft-12", "draft_id": "r-somebody-elses"}
        r = signed("agent", "POST", f"/api/connections/{mailbox}/draft-update", foreign)
        assert r.status_code == 409 and "Only drafts Vexa created" in r.json()["detail"]
        # Another mailbox of the same person did not create it either.
        other = connection("google_email")
        store.put(other, {"owner": OWNER, "value": COMPOSE})
        ready(other)
        r = signed("agent", "POST", f"/api/connections/{other}/draft-update", {**update, "request_id": "fixture-draft-13"})
        assert r.status_code == 409
        assert put.call_count == 1


def test_draft_policy_keys_refuse_by_name(signed, mailbox):
    base = {"request_id": "fixture-draft-20", "recipient": "ada@example.test", "subject": "s", "body": "b"}
    pdf = {"filename": "a.pdf", "content_type": "application/pdf", "data": base64.b64encode(b"x" * 2048).decode()}
    cases = [
        ({"cc": ["eve@outside.test"], "policy": {"recipient_domains": ["example.test"]}}, "draft_recipient_domains"),
        ({"attachments": [pdf]}, "draft_attachments"),
        ({"attachments": [pdf], "policy": {"attachments": True, "attachment_max_bytes": 1024}}, "draft_attachment_max_kb"),
    ]
    with patch.object(providers, "create_gmail_draft", return_value=CREATED) as create:
        for i, (change, key) in enumerate(cases):
            r = signed("agent", "POST", f"/api/connections/{mailbox}/draft", {**base, "request_id": f"fixture-draft-2{i}", **change})
            assert r.status_code == 409 and r.json()["detail"].startswith(f"Refused by policy {key}:"), (key, r.text)
        create.assert_not_called()
        ok = {**base, "request_id": "fixture-draft-29", "attachments": [pdf],
              "policy": {"attachments": True, "attachment_max_bytes": 4096, "recipient_domains": ["example.test"]}}
        assert signed("agent", "POST", f"/api/connections/{mailbox}/draft", ok).status_code == 200
        assert create.call_args.kwargs["attachments"] == [("a.pdf", "application/pdf", b"x" * 2048)]


def test_the_retry_of_a_draft_made_before_this_release_still_matches(signed, mailbox, broker):
    """Fingerprints written before drafts gained a kind prefix keep answering their retries."""
    import hashlib
    import hmac as _hmac
    payload = {"request_id": "fixture-draft-30", "recipient": "ada@example.test", "subject": "s", "body": "b"}
    legacy = _hmac.new(broker.hmac_key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()
    broker.sql("INSERT INTO draft_requests VALUES (?,?,?,?,?)", ("fixture-draft-30", OWNER, mailbox, legacy,
                                                               json.dumps(CREATED)))
    assert signed("agent", "POST", f"/api/connections/{mailbox}/draft", payload).json() == CREATED
