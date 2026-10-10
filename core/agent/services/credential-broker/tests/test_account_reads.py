"""Gmail and Calendar reads: fixed hosts, bounded results, owner-scoped, audited without content."""
import base64
import json
import threading
import time
from unittest.mock import patch

import httpx
import pytest

from credential_broker import providers
from conftest import OWNER


def test_gmail_read_projects_content_not_credentials():
    def respond(req):
        assert req.url.host == "gmail.googleapis.com" and req.method == "GET"
        assert req.headers["authorization"] == "Bearer private-token"
        return httpx.Response(200, json={"id": "abc", "payload": {"mimeType": "text/plain", "body": {
            "data": base64.urlsafe_b64encode(b"fixture mail").decode()}, "headers": [{"name": "Subject", "value": "fixture"}]},
            "access_token": "must-not-return"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        r = providers.read_account("google_email", {"access_token": "private-token"}, "gmail.read", message_id="abc", http=c)
    assert r["message"]["body"] == "fixture mail"
    assert "token" not in json.dumps(r)


def test_search_fixed_host_and_bound_results():
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={"messages": [{"id": "abc"}], "nextPageToken": "opaque"}
                              if req.url.path.endswith("/messages") else {"id": "abc", "payload": {}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        r = providers.read_account("google_email", {"access_token": "x"}, "gmail.search", query="from:fixture.test", http=c)
    assert len(r["messages"]) == 1 and r["has_more"]
    assert calls[0].url.params["q"] == "from:fixture.test"


def test_wrong_provider_paths_and_time_range_refused():
    with httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network forbidden"))) as c:
        for p, a, kw in [("google_calendar", "gmail.search", {}), ("google_email", "gmail.read", {"message_id": "../../secret"}),
                         ("google_calendar", "calendar.events", {"time_min": "bad", "time_max": "bad"})]:
            with pytest.raises(providers.ProviderError):
                providers.read_account(p, {"access_token": "x"}, a, http=c, **kw)


@pytest.mark.parametrize("provider,action,extra", [
    ("google_email", "gmail.search", {}),
    ("google_calendar", "calendar.events", {"time_min": "2026-10-01T00:00:00Z", "time_max": "2026-10-02T00:00:00Z"})])
def test_both_providers_forward_and_return_cursor(provider, action, extra):
    def respond(req):
        assert req.url.params["pageToken"] == "second"
        return httpx.Response(200, json={"nextPageToken": "third", "messages": [], "items": []})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        result = providers.read_account(provider, {"access_token": "private"}, action, page_token="second", http=c, **extra)
    assert result["next_page_token"] == "third"


@pytest.mark.parametrize("code,fragment", [(400, "arguments"), (401, "Authorization"), (403, "scopes"),
                                           (404, "not found")])
def test_provider_failures_are_classified_without_body_leak(code, fragment):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, text="PRIVATE"))) as c:
        with pytest.raises(providers.ProviderError) as e:
            providers.read_account("google_email", {"access_token": "private"}, "gmail.search", http=c)
    assert fragment in str(e.value) and "PRIVATE" not in str(e.value)


def test_read_is_owner_scoped_ready_only_and_audited_without_content(signed, connection, ready, store, broker):
    cid = connection("google_email")
    body = {"action": "gmail.search", "query": "private search"}
    path = f"/api/connections/{cid}/read"
    assert signed("agent", "POST", path, body, actor="other").status_code == 404
    assert signed("agent", "POST", path, body).status_code == 409
    assert not store.calls
    store.put(cid, {"owner": OWNER, "value": {"access_token": "SECRET", "expires_at": time.time() + 300}})
    ready(cid)
    with patch.object(providers, "read_account", return_value={"messages": [{"id": "abc"}]}):
        r = signed("agent", "POST", path, body)
    assert r.status_code == 200 and "SECRET" not in r.text
    assert r.json()["untrusted_content"] is True and r.json()["source"] == "google_email"
    audit = broker.sql("SELECT * FROM audit WHERE connection=?", (cid,), rows=True)
    assert any(a["action"] == "gmail.search" and a["outcome"] == "success" for a in audit)
    assert "private search" not in json.dumps(audit) and "SECRET" not in json.dumps(audit)


def test_expired_token_is_refreshed_and_rotated(signed, connection, ready, store):
    cid = connection("google_calendar")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "old", "refresh_token": "r", "expires_at": 0, "scope": "s"}})
    ready(cid)
    with patch.object(providers, "refresh", return_value={"access_token": "new", "refresh_token": "r2", "expires_at": time.time() + 3600, "scope": "s"}), \
            patch.object(providers, "read_account", return_value={"events": []}) as read:
        r = signed("agent", "POST", f"/api/connections/{cid}/read",
                   {"action": "calendar.events", "time_min": "2026-10-01T00:00:00Z", "time_max": "2026-10-02T00:00:00Z"})
    assert r.status_code == 200
    assert read.call_args.args[1]["access_token"] == "new"
    assert store.rows[cid][-1]["value"]["refresh_token"] == "r2"


def test_provider_read_does_not_hold_the_global_lock(signed, connection, ready, store, broker):
    cid = connection("google_email")
    store.put(cid, {"owner": OWNER, "value": {"access_token": "private", "expires_at": time.time() + 3600}})
    ready(cid)

    def read(*args, **kwargs):
        acquired = []

        def probe():
            held = broker.lock.acquire(timeout=.2)
            acquired.append(held)
            if held:
                broker.lock.release()
        t = threading.Thread(target=probe)
        t.start()
        t.join()
        assert acquired == [True]
        return {"messages": []}
    with patch.object(providers, "read_account", side_effect=read):
        assert signed("agent", "POST", f"/api/connections/{cid}/read", {"action": "gmail.search"}).status_code == 200


def test_thread_pagination_keeps_older_messages_and_participants():
    def respond(req):
        assert req.url.path == "/gmail/v1/users/me/threads/thread1"
        return httpx.Response(200, json={"id": "thread1", "messages": [{"id": str(i), "payload": {"headers": [
            {"name": "Cc", "value": "person@example.test"}, {"name": "Message-ID", "value": "source-message"}],
            "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"full older context").decode()}}} for i in range(3)]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as c:
        first = providers.read_account("google_email", {"access_token": "secret"}, "gmail.thread", message_id="thread1", limit=2, http=c)
        second = providers.read_account("google_email", {"access_token": "secret"}, "gmail.thread", message_id="thread1",
                                        limit=2, page_token=first["next_page_token"], http=c)
    assert first["messages"][0]["headers"]["cc"] == "person@example.test"
    assert first["messages"][0]["headers"]["message-id"] == "source-message"
    assert second["messages"][0]["body"] == "full older context" and not second["has_more"]


def test_calendar_preserves_relationship_and_cancellation_evidence():
    event = {"id": "e", "status": "cancelled", "attendees": [{"email": "person@example.test", "responseStatus": "declined"}],
             "organizer": {"email": "host@example.test"}, "recurringEventId": "series", "attendeesOmitted": True}
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"items": [event]}))) as c:
        result = providers.read_account("google_calendar", {"access_token": "secret"}, "calendar.events",
                                        time_min="2026-07-01T00:00:00Z", time_max="2026-10-01T00:00:00Z", http=c)
    for key, value in event.items():
        assert result["events"][0][key] == value
