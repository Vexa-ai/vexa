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
