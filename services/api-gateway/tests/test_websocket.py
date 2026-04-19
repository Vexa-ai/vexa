"""WebSocket multiplex endpoint tests for api-gateway.

Tests the /ws endpoint: auth, subscribe/unsubscribe, ping, error handling.
Uses starlette.testclient.TestClient for synchronous WebSocket testing.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient
from main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_app_state():
    """Patch app.state.redis and app.state.http_client so the WS handler
    can run without real infrastructure."""
    # Fake Redis pubsub that yields nothing (blocks until cancelled)
    fake_pubsub = AsyncMock()
    fake_pubsub.subscribe = AsyncMock()
    fake_pubsub.unsubscribe = AsyncMock()
    fake_pubsub.close = AsyncMock()

    async def _empty_listen():
        """Async generator that never yields (simulates idle pubsub)."""
        # We need to actually block, so use an event that never gets set
        import asyncio
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return
        # Make this an async generator
        yield  # pragma: no cover – never reached

    fake_pubsub.listen = _empty_listen

    fake_redis = AsyncMock()
    fake_redis.pubsub.return_value = fake_pubsub

    fake_http = AsyncMock()

    app.state.redis = fake_redis
    app.state.http_client = fake_http

    yield

    # Cleanup
    del app.state.redis
    del app.state.http_client


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestWSAuth:
    def test_connect_without_api_key_gets_error_and_close(self):
        """WS connect without api_key → error message then close."""
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "missing_api_key"

    def test_connect_with_api_key_accepted(self):
        """WS connect with api_key query param → connection stays open."""
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            # Send a ping to verify the connection is live
            ws.send_json({"action": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"

    def test_connect_with_header_api_key(self):
        """WS connect with X-API-Key header → connection stays open."""
        client = TestClient(app)
        with client.websocket_connect("/ws", headers={"x-api-key": "test-key"}) as ws:
            ws.send_json({"action": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"


# ---------------------------------------------------------------------------
# Ping / unknown action
# ---------------------------------------------------------------------------

class TestWSActions:
    def test_ping_pong(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "ping"})
            assert ws.receive_json() == {"type": "pong"}

    def test_unknown_action(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "foobar"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "unknown_action"

    def test_invalid_json(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_text("not json at all")
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "invalid_json"


# ---------------------------------------------------------------------------
# Subscribe tests
# ---------------------------------------------------------------------------

class TestWSSubscribe:
    def test_subscribe_missing_meetings_field(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "subscribe"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "invalid_subscribe_payload"

    def test_subscribe_empty_meetings_list(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "subscribe", "meetings": []})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "empty" in msg["details"]

    def test_subscribe_meetings_not_list(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "subscribe", "meetings": "bad"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "invalid_subscribe_payload"

    def test_subscribe_no_valid_meeting_objects(self):
        """meetings list with items that lack platform/native_id → error."""
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "subscribe", "meetings": [{"bad": "data"}]})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "no valid meeting objects" in msg.get("details", "")

    @patch("main._resolve_token", AsyncMock(return_value=None))
    def test_subscribe_success(self):
        """Valid subscribe with mocked authorization → subscribed response."""
        # _resolve_token patched to None so the WS handler skips user-header
        # injection — the single auth_response mock below is not a valid
        # /internal/validate payload and would KeyError if consumed there.
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "authorized": [
                {"platform": "google_meet", "native_id": "abc-def", "user_id": "u1", "meeting_id": "m1"}
            ],
            "errors": [],
        }
        app.state.http_client.post = AsyncMock(return_value=auth_response)

        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            ws.send_json({
                "action": "subscribe",
                "meetings": [{"platform": "google_meet", "native_id": "abc-def"}],
            })
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert len(msg["meetings"]) == 1
            assert msg["meetings"][0]["platform"] == "google_meet"

    def test_subscribe_auth_failure(self):
        """Authorization service returns non-200 → error forwarded."""
        auth_response = MagicMock()
        auth_response.status_code = 403
        auth_response.text = "Forbidden"
        app.state.http_client.post = AsyncMock(return_value=auth_response)

        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            ws.send_json({
                "action": "subscribe",
                "meetings": [{"platform": "google_meet", "native_id": "abc-def"}],
            })
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "authorization_service_error"

    def test_subscribe_auth_exception(self):
        """Authorization HTTP call raises → authorization_call_failed error."""
        app.state.http_client.post = AsyncMock(side_effect=Exception("connection refused"))

        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            ws.send_json({
                "action": "subscribe",
                "meetings": [{"platform": "google_meet", "native_id": "abc-def"}],
            })
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "authorization_call_failed"


# ---------------------------------------------------------------------------
# Unsubscribe tests
# ---------------------------------------------------------------------------

class TestWSUnsubscribe:
    def test_unsubscribe_not_subscribed(self):
        """Unsubscribe from a meeting we never subscribed to → error."""
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({
                "action": "unsubscribe",
                "meetings": [{"platform": "google_meet", "native_id": "xyz"}],
            })
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "not currently subscribed" in str(msg.get("details", ""))

    def test_unsubscribe_bad_payload(self):
        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=k") as ws:
            ws.send_json({"action": "unsubscribe", "meetings": "bad"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["error"] == "invalid_unsubscribe_payload"

    @patch("main._resolve_token", AsyncMock(return_value=None))
    def test_subscribe_then_unsubscribe(self):
        """Subscribe then unsubscribe → both succeed."""
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "authorized": [
                {"platform": "google_meet", "native_id": "room1", "user_id": "u1", "meeting_id": "m1"}
            ],
            "errors": [],
        }
        app.state.http_client.post = AsyncMock(return_value=auth_response)

        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            # Subscribe
            ws.send_json({
                "action": "subscribe",
                "meetings": [{"platform": "google_meet", "native_id": "room1"}],
            })
            sub_msg = ws.receive_json()
            assert sub_msg["type"] == "subscribed"

            # Unsubscribe
            ws.send_json({
                "action": "unsubscribe",
                "meetings": [{"platform": "google_meet", "native_id": "room1"}],
            })
            unsub_msg = ws.receive_json()
            assert unsub_msg["type"] == "unsubscribed"
            assert len(unsub_msg["meetings"]) == 1


# ---------------------------------------------------------------------------
# Multiple subscriptions
# ---------------------------------------------------------------------------

class TestWSMultipleSubscriptions:
    @patch("main._resolve_token", AsyncMock(return_value=None))
    def test_subscribe_multiple_meetings(self):
        """Subscribe to two meetings at once → both confirmed."""
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "authorized": [
                {"platform": "google_meet", "native_id": "room1", "user_id": "u1", "meeting_id": "m1"},
                {"platform": "teams", "native_id": "room2", "user_id": "u1", "meeting_id": "m2"},
            ],
            "errors": [],
        }
        app.state.http_client.post = AsyncMock(return_value=auth_response)

        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            ws.send_json({
                "action": "subscribe",
                "meetings": [
                    {"platform": "google_meet", "native_id": "room1"},
                    {"platform": "teams", "native_id": "room2"},
                ],
            })
            msg = ws.receive_json()
            assert msg["type"] == "subscribed"
            assert len(msg["meetings"]) == 2
            platforms = {m["platform"] for m in msg["meetings"]}
            assert platforms == {"google_meet", "teams"}

    @patch("main._resolve_token", AsyncMock(return_value=None))
    def test_duplicate_subscribe_is_idempotent(self):
        """Subscribing to the same meeting twice doesn't break anything."""
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "authorized": [
                {"platform": "google_meet", "native_id": "room1", "user_id": "u1", "meeting_id": "m1"},
            ],
            "errors": [],
        }
        app.state.http_client.post = AsyncMock(return_value=auth_response)

        client = TestClient(app)
        with client.websocket_connect("/ws?api_key=test-key") as ws:
            # First subscribe
            ws.send_json({
                "action": "subscribe",
                "meetings": [{"platform": "google_meet", "native_id": "room1"}],
            })
            msg1 = ws.receive_json()
            assert msg1["type"] == "subscribed"

            # Second subscribe (same meeting)
            ws.send_json({
                "action": "subscribe",
                "meetings": [{"platform": "google_meet", "native_id": "room1"}],
            })
            msg2 = ws.receive_json()
            assert msg2["type"] == "subscribed"
