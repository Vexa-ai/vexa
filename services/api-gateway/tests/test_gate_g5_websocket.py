"""G5 gate — WebSocket live transcription delivery from Redis pub/sub.

Integration test: requires running compose stack (api-gateway + Redis + meeting-api).
Skips automatically if the gateway is not reachable on localhost:8056.
"""
import asyncio
import json
import os
import subprocess

import httpx
import pytest

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8056")
ADMIN_TOKEN = os.environ.get("ADMIN_API_TOKEN", "changeme")
REDIS_CONTAINER = os.environ.get("REDIS_CONTAINER", "vexa-restore-redis-1")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "vexa-redis-dev")

# ---------------------------------------------------------------------------
# Skip if gateway is not running
# ---------------------------------------------------------------------------

def _gateway_reachable() -> bool:
    try:
        r = httpx.get(f"{GATEWAY}/", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _gateway_reachable(),
    reason="api-gateway not reachable — skipping G5 integration test",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin(method: str, path: str, **kwargs) -> httpx.Response:
    return httpx.request(
        method, f"{GATEWAY}{path}",
        headers={"X-Admin-API-Key": ADMIN_TOKEN},
        timeout=10,
        **kwargs,
    )


def _redis_publish(channel: str, payload: str) -> int:
    """Publish a message to a Redis channel via docker exec."""
    result = subprocess.run(
        [
            "docker", "exec", REDIS_CONTAINER,
            "redis-cli", "-a", REDIS_PASSWORD,
            "PUBLISH", channel, payload,
        ],
        capture_output=True, text=True, timeout=10,
    )
    # Output is "(integer) N" where N is the number of subscribers who got it
    return int(result.stdout.strip().split()[-1]) if result.returncode == 0 else 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def api_token() -> str:
    """Create a test user and return a valid API token."""
    # Create user (ignore 409 if already exists)
    _admin("POST", "/admin/users", json={
        "email": "g5-ws-gate@test.com",
        "name": "G5 WS Gate",
    })
    # Find the user
    users_resp = _admin("GET", "/admin/users")
    users = users_resp.json()
    user_id = None
    for u in users:
        if u.get("email") == "g5-ws-gate@test.com":
            user_id = u["id"]
            break
    assert user_id is not None, "Failed to find/create test user"

    # Create token
    tok_resp = _admin("POST", f"/admin/users/{user_id}/tokens", json={"name": "g5-gate"})
    return tok_resp.json()["token"]


@pytest.fixture(scope="module")
def meeting_info(api_token: str) -> dict:
    """Create a meeting for the test user so subscribe is authorized."""
    r = httpx.post(
        f"{GATEWAY}/bots",
        headers={"X-API-Key": api_token},
        json={
            "platform": "google_meet",
            "native_meeting_id": "g5-ws-gate-room",
            "bot_name": "G5 Gate Bot",
        },
        timeout=15,
    )
    # Bot may fail to start (no infra), but meeting should still be created
    meetings = httpx.get(
        f"{GATEWAY}/meetings",
        headers={"X-API-Key": api_token},
        timeout=10,
    ).json()["meetings"]
    match = [m for m in meetings if m["native_meeting_id"] == "g5-ws-gate-room"]
    assert match, "Meeting was not created"
    return {"meeting_id": match[0]["id"], "native_meeting_id": "g5-ws-gate-room"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestG5WebSocketLiveDelivery:
    """G5: WebSocket receives live transcription segments from Redis pub/sub."""

    def test_subscribe_and_receive_transcription(self, api_token: str, meeting_info: dict):
        """Connect WS → subscribe → publish to Redis → assert message arrives."""
        import websockets

        meeting_id = meeting_info["meeting_id"]

        async def run():
            uri = f"ws://localhost:8056/ws?api_key={api_token}"
            async with websockets.connect(uri) as ws:
                # Subscribe
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}],
                }))
                sub = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert sub["type"] == "subscribed", f"Expected subscribed, got: {sub}"
                assert len(sub["meetings"]) == 1

                # Publish transcription segment to Redis
                segment = {
                    "speaker": "Alice",
                    "text": "Hello from G5 gate test",
                    "start_time": "2026-03-27T10:00:00Z",
                    "type": "transcription_segment",
                }
                channel = f"tc:meeting:{meeting_id}:mutable"
                receivers = _redis_publish(channel, json.dumps(segment))
                assert receivers >= 1, f"Expected >=1 subscriber, got {receivers}"

                # Receive the forwarded message
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                assert msg["speaker"] == "Alice"
                assert msg["text"] == "Hello from G5 gate test"
                assert msg["type"] == "transcription_segment"

        asyncio.run(run())

    def test_subscribe_and_receive_bot_status(self, api_token: str, meeting_info: dict):
        """Bot-manager status messages also arrive over the same subscription."""
        import websockets

        meeting_id = meeting_info["meeting_id"]

        async def run():
            uri = f"ws://localhost:8056/ws?api_key={api_token}"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}],
                }))
                sub = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert sub["type"] == "subscribed"

                # Publish bot status to the bm: channel
                status = {"type": "bot_status", "status": "recording", "meeting_id": meeting_id}
                channel = f"bm:meeting:{meeting_id}:status"
                receivers = _redis_publish(channel, json.dumps(status))
                assert receivers >= 1

                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                assert msg["type"] == "bot_status"
                assert msg["status"] == "recording"

        asyncio.run(run())

    def test_subscribe_and_receive_chat(self, api_token: str, meeting_info: dict):
        """Chat messages from bot arrive on va:meeting:{id}:chat channel."""
        import websockets

        meeting_id = meeting_info["meeting_id"]

        async def run():
            uri = f"ws://localhost:8056/ws?api_key={api_token}"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}],
                }))
                sub = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert sub["type"] == "subscribed"

                # Publish chat message
                chat_msg = {"type": "chat_message", "sender": "Bot", "text": "Meeting summary ready"}
                channel = f"va:meeting:{meeting_id}:chat"
                receivers = _redis_publish(channel, json.dumps(chat_msg))
                assert receivers >= 1

                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                msg = json.loads(raw)
                assert msg["type"] == "chat_message"
                assert msg["text"] == "Meeting summary ready"

        asyncio.run(run())

    def test_multiple_segments_in_order(self, api_token: str, meeting_info: dict):
        """Multiple segments published in sequence arrive in order."""
        import websockets

        meeting_id = meeting_info["meeting_id"]

        async def run():
            uri = f"ws://localhost:8056/ws?api_key={api_token}"
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}],
                }))
                sub = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert sub["type"] == "subscribed"

                channel = f"tc:meeting:{meeting_id}:mutable"
                texts = ["First segment", "Second segment", "Third segment"]
                for text in texts:
                    _redis_publish(channel, json.dumps({"text": text}))
                    await asyncio.sleep(0.05)  # tiny gap to preserve ordering

                received = []
                for _ in range(len(texts)):
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    received.append(json.loads(raw)["text"])

                assert received == texts, f"Expected {texts}, got {received}"

        asyncio.run(run())
