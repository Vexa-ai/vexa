"""The bot-account tools (ADR-0049): what each sends to the gateway, and what the agent gets back.

`browser_session_open` hands the client what it needs to open the live view (`ui_action` plus the
session fields); `bot_accounts_list` and `bot_account_verify` wrap their routes; request_meeting_bot
passes `bot_account` through.
"""
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from vexa_mcp import create_app

AUTH = {"Authorization": "Bearer vxa_test"}
SID = "0123456789abcdef0123456789abcdef"
ACCOUNT = "fedcba9876543210fedcba9876543210"
SESSION = {"session_id": SID, "status": "starting", "platform": "google", "owner": "user:7",
           "view_path": f"/bot-accounts/sessions/{SID}/view/vnc.html",
           "ws_path": f"/bot-accounts/sessions/{SID}/vnc", "vnc_password": "pw-123",
           "expires_at": "2026-10-13T12:00:00Z"}


def _client(reply=None):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(201 if request.url.path.endswith("/sessions") else 200,
                              json=reply if reply is not None else {"ok": True})

    return TestClient(create_app("http://gateway.test", transport=httpx.MockTransport(handler))), sent


def test_browser_session_open_posts_the_session_and_names_the_ui_action():
    client, sent = _client(SESSION)
    r = client.post("/browser-session", headers=AUTH,
                    json={"platform": "google", "display_name": "Notetaker", "owner": "company"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ui_action"] == "open_browser_session"
    for field in ("session_id", "vnc_password", "platform", "view_path", "ws_path"):
        assert body[field] == SESSION[field]
    req = sent[-1]
    assert (req.method, req.url.path) == ("POST", "/bot-accounts/sessions")
    assert json.loads(req.content) == {"platform": "google", "display_name": "Notetaker",
                                       "owner": "company"}
    assert req.headers["x-api-key"] == "vxa_test"


def test_browser_session_open_refuses_a_platform_it_cannot_sign_in_to():
    client, sent = _client(SESSION)
    assert client.post("/browser-session", headers=AUTH, json={"platform": "jitsi"}).status_code == 422
    assert client.post("/browser-session", headers=AUTH,
                       json={"platform": "google", "account_id": "nope"}).status_code == 422
    assert sent == []


def test_bot_accounts_list_reads_the_accounts():
    reply = {"accounts": [], "company_bot_use": "all"}
    client, sent = _client(reply)
    r = client.get("/bot-accounts", headers=AUTH)
    assert r.json() == reply
    assert (sent[-1].method, sent[-1].url.path) == ("GET", "/bot-accounts")


def test_bot_account_verify_posts_to_the_account():
    client, sent = _client({"account_id": ACCOUNT, "check_id": "c" * 32, "status": "checking"})
    r = client.post("/bot-account-verify", headers=AUTH, json={"account_id": ACCOUNT})
    assert r.json()["status"] == "checking"
    assert (sent[-1].method, sent[-1].url.path) == ("POST", f"/bot-accounts/{ACCOUNT}/verify")


def test_request_meeting_bot_passes_bot_account_through():
    client, sent = _client({"id": 1})
    r = client.post("/request-meeting-bot", headers=AUTH,
                    json={"native_meeting_id": "abc-defg-hij", "bot_account": ACCOUNT})
    assert r.status_code == 200, r.text
    assert json.loads(sent[-1].content)["bot_account"] == ACCOUNT
    assert client.post("/request-meeting-bot", headers=AUTH,
                       json={"native_meeting_id": "abc-defg-hij", "bot_account": "x"}).status_code == 422
