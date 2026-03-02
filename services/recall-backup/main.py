"""
Recall Backup Service

Listens for Vexa bot failures and automatically creates Recall.ai bots as fallback.
Recall bot connects to our WebSocket endpoint and streams audio_mixed_raw events.
We bridge audio (S16LE → Float32) to WhisperLive for transcription.

Flow:
1. Bot Manager detects Vexa bot failure → publishes to Redis channel `vexa:bot:failure`
2. This service picks up the event, creates Recall bot with WS endpoint pointing at us
3. Recall bot joins meeting → opens WebSocket to our /recall/ws/{meeting_key}
4. We receive audio_mixed_raw.data frames, convert S16LE → Float32, forward to WhisperLive
5. WhisperLive produces transcriptions through the normal Redis pipeline
6. The customer sees no difference — same transcript format.
"""

import os
import json
import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx
import numpy as np
import redis.asyncio as redis
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

app = FastAPI(title="Recall Backup Service")

# --- Config ---

RECALL_API_KEY = os.getenv("RECALL_API_KEY", "")
RECALL_BASE_URL = os.getenv("RECALL_BASE_URL", "https://us-west-2.recall.ai/api/v1")
RECALL_BOT_NAME = os.getenv("RECALL_BOT_NAME", "Meeting Assistant")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
WHISPERLIVE_URL = os.getenv("WHISPERLIVE_URL", "ws://whisperlive:9090/ws")
CALLBACK_BASE_URL = os.getenv("CALLBACK_BASE_URL", "http://recall-backup:8090")

# --- State ---


@dataclass
class RecallSession:
    """Tracks an active Recall backup session."""
    meeting_key: str
    meeting_url: str
    meeting_id: int
    user_id: int
    platform: str
    native_meeting_id: str
    session_uid: str
    token: str  # JWT for WhisperLive
    recall_bot_id: str
    wl_ws: Optional[websockets.WebSocketClientProtocol] = field(default=None, repr=False)
    wl_ready: bool = False
    audio_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _wl_task: Optional[asyncio.Task] = field(default=None, repr=False)


active_sessions: dict[str, RecallSession] = {}  # meeting_key → RecallSession


# --- Recall API Client ---

class RecallClient:
    """Thin wrapper around Recall.ai REST API."""

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Token {api_key}"},
            timeout=30.0,
        )

    async def create_bot(
        self,
        meeting_url: str,
        ws_url: str,
        bot_name: str = RECALL_BOT_NAME,
    ) -> dict:
        """Create a Recall bot that streams raw audio to our WebSocket endpoint."""
        body = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                "transcript": {
                    "provider": {"meeting_captions": {}},
                },
                "audio_mixed_raw": {},
                "realtime_endpoints": [
                    {
                        "type": "websocket",
                        "url": ws_url,
                        "events": [
                            "audio_mixed_raw.data",
                            "participant_events.join",
                            "participant_events.leave",
                        ],
                    }
                ],
            },
        }

        resp = await self.client.post("/bot/", json=body)
        if resp.status_code >= 400:
            logger.error(f"Recall API error {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    async def get_bot(self, bot_id: str) -> dict:
        resp = await self.client.get(f"/bot/{bot_id}/")
        resp.raise_for_status()
        return resp.json()

    async def leave_call(self, bot_id: str) -> dict:
        resp = await self.client.post(f"/bot/{bot_id}/leave_call/")
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self.client.aclose()


recall_client: Optional[RecallClient] = None
redis_pool: Optional[redis.Redis] = None


# --- WhisperLive Bridge (outbound WS) ---

async def whisperlive_bridge(session: RecallSession):
    """
    Maintain a WebSocket connection to WhisperLive and forward audio from the queue.

    Sends initial config JSON, waits for SERVER_READY, then streams Float32 audio frames.
    Reconnects with exponential backoff on disconnect.
    """
    backoff = 1.0
    max_backoff = 30.0

    while True:
        try:
            async with websockets.connect(WHISPERLIVE_URL) as ws:
                session.wl_ws = ws
                session.wl_ready = False
                backoff = 1.0  # reset on successful connect

                # Send initial handshake config
                config = {
                    "uid": session.session_uid,
                    "platform": session.platform,
                    "meeting_url": session.meeting_url,
                    "token": session.token,
                    "meeting_id": session.native_meeting_id,
                    "language": None,
                    "task": "transcribe",
                    "use_vad": True,
                }
                await ws.send(json.dumps(config))
                logger.info(f"[{session.meeting_key}] WL bridge: sent config to WhisperLive")

                # Wait for SERVER_READY
                try:
                    resp_raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
                    resp = json.loads(resp_raw)
                    if resp.get("status") == "SERVER_READY":
                        session.wl_ready = True
                        logger.info(f"[{session.meeting_key}] WL bridge: WhisperLive ready")
                    else:
                        logger.warning(f"[{session.meeting_key}] WL bridge: unexpected response: {resp}")
                        continue  # reconnect
                except asyncio.TimeoutError:
                    logger.warning(f"[{session.meeting_key}] WL bridge: timeout waiting for SERVER_READY")
                    continue  # reconnect

                # Consume audio queue and forward to WhisperLive
                while True:
                    audio_bytes = await session.audio_queue.get()

                    # Sentinel: None means session ended
                    if audio_bytes is None:
                        logger.info(f"[{session.meeting_key}] WL bridge: received stop sentinel")
                        return

                    # Convert S16LE → Float32 and send as binary frame
                    pcm_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
                    pcm_float32 = (pcm_int16.astype(np.float32) / 32768.0).tobytes()
                    await ws.send(pcm_float32)

        except websockets.ConnectionClosed as e:
            logger.warning(f"[{session.meeting_key}] WL bridge: connection closed ({e}), reconnecting in {backoff}s")
        except Exception as e:
            logger.error(f"[{session.meeting_key}] WL bridge: error ({e}), reconnecting in {backoff}s", exc_info=True)

        session.wl_ws = None
        session.wl_ready = False
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


# --- Lifecycle ---

@app.on_event("startup")
async def startup():
    global recall_client, redis_pool

    redis_pool = redis.from_url(REDIS_URL, decode_responses=True)
    logger.info("Redis pool created")

    if RECALL_API_KEY:
        recall_client = RecallClient(RECALL_API_KEY, RECALL_BASE_URL)
        logger.info("Recall backup service started with API key configured")
    else:
        logger.warning("RECALL_API_KEY not set — backup service disabled")

    asyncio.create_task(listen_for_failures())


@app.on_event("shutdown")
async def shutdown():
    # Clean up all active sessions
    for session in list(active_sessions.values()):
        await cleanup_session(session)

    if recall_client:
        await recall_client.close()
    if redis_pool:
        await redis_pool.aclose()


# --- Redis Listener: Bot Failures ---

async def listen_for_failures():
    """Listen for Vexa bot failure events on Redis and trigger Recall backup.

    Uses get_message() polling instead of async-for to avoid the
    ``aclose(): asynchronous generator is already running`` bug in
    redis.asyncio pubsub.  Reconnects on any Redis error.
    """
    while True:
        r = None
        pubsub = None
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe("vexa:bot:failure")
            logger.info("Subscribed to vexa:bot:failure")

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    await asyncio.sleep(0.1)
                    continue

                await _handle_failure_message(message)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Failure listener error: {e}", exc_info=True)
        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe("vexa:bot:failure")
                    await pubsub.aclose()
                except Exception:
                    pass
            if r:
                try:
                    await r.aclose()
                except Exception:
                    pass

        logger.info("Failure listener reconnecting in 3s...")
        await asyncio.sleep(3)


async def _handle_failure_message(message: dict):
    """Process a single failure event from Redis pubsub."""
    try:
        data = json.loads(message["data"])
        meeting_key = data.get("meeting_key")
        meeting_url = data.get("meeting_url")
        failure_reason = data.get("reason")

        logger.info(f"Bot failure detected: {meeting_key} reason={failure_reason}")

        if not recall_client or not meeting_url:
            return

        # Don't double-backup
        if meeting_key in active_sessions:
            logger.info(f"Recall backup already active for {meeting_key}")
            return

        # Build WebSocket URL for Recall to connect to us
        # CALLBACK_BASE_URL is http(s)://... — convert to ws(s)://...
        ws_base = CALLBACK_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_base}/recall/ws/{meeting_key}"

        # Create Recall bot with our WS endpoint
        bot = await recall_client.create_bot(
            meeting_url=meeting_url,
            ws_url=ws_url,
        )

        # Create session with all enriched fields from bot-manager
        session = RecallSession(
            meeting_key=meeting_key,
            meeting_url=meeting_url,
            meeting_id=data.get("meeting_id", 0),
            user_id=data.get("user_id", 0),
            platform=data.get("platform", ""),
            native_meeting_id=data.get("native_meeting_id", ""),
            session_uid=data.get("session_uid", ""),
            token=data.get("token", ""),
            recall_bot_id=bot["id"],
        )

        # Start WhisperLive bridge (outbound WS)
        session._wl_task = asyncio.create_task(whisperlive_bridge(session))
        active_sessions[meeting_key] = session

        logger.info(f"Recall backup bot created: {bot['id']} for {meeting_key}, ws_url={ws_url}")

        # Notify bot-manager that backup is active
        if redis_pool:
            await redis_pool.publish(
                "vexa:bot:backup_active",
                json.dumps({
                    "meeting_key": meeting_key,
                    "recall_bot_id": bot["id"],
                    "user_id": session.user_id,
                }),
            )

    except Exception as e:
        logger.error(f"Error handling bot failure: {e}", exc_info=True)


# --- Session Cleanup ---

async def cleanup_session(session: RecallSession):
    """Clean up a Recall backup session."""
    # Push sentinel to stop the WL bridge
    await session.audio_queue.put(None)

    # Cancel WL bridge task
    if session._wl_task and not session._wl_task.done():
        session._wl_task.cancel()
        try:
            await session._wl_task
        except asyncio.CancelledError:
            pass

    # Remove from active sessions
    active_sessions.pop(session.meeting_key, None)
    logger.info(f"Cleaned up session for {session.meeting_key}")


# --- Inbound WebSocket: Recall sends audio/events to us ---

@app.websocket("/recall/ws/{meeting_key}")
async def recall_ws_endpoint(ws: WebSocket, meeting_key: str):
    """
    WebSocket endpoint that Recall.ai connects to.

    Recall sends JSON frames with event types:
    - audio_mixed_raw.data: base64-encoded 16kHz mono S16LE PCM
    - participant_events.*: join/leave notifications
    """
    await ws.accept()
    logger.info(f"[{meeting_key}] Recall WS connected")

    session = active_sessions.get(meeting_key)
    if not session:
        logger.warning(f"[{meeting_key}] Recall WS: no active session, closing")
        await ws.close(code=4000, reason="No active session")
        return

    try:
        while True:
            raw = await ws.receive_text()
            event = json.loads(raw)
            event_type = event.get("event", "")

            if event_type == "audio_mixed_raw.data":
                # Extract base64 audio buffer and push to queue
                b64_audio = event.get("data", {}).get("data", "")
                if not b64_audio:
                    # Try alternate field name
                    b64_audio = event.get("data", {}).get("buffer", "")
                if b64_audio:
                    try:
                        raw_bytes = base64.b64decode(b64_audio)
                        await session.audio_queue.put(raw_bytes)
                    except Exception as e:
                        logger.error(f"[{meeting_key}] Error decoding audio: {e}")

            elif event_type.startswith("participant_events."):
                await forward_participant_event(session, event_type, event.get("data", {}))

    except WebSocketDisconnect:
        logger.info(f"[{meeting_key}] Recall WS disconnected")
    except Exception as e:
        logger.error(f"[{meeting_key}] Recall WS error: {e}", exc_info=True)
    finally:
        # Recall disconnected — clean up session
        logger.info(f"[{meeting_key}] Recall WS ended, cleaning up session")
        await cleanup_session(session)


async def forward_participant_event(
    session: RecallSession, event_type: str, event_data: dict
):
    """Forward Recall participant events to speaker_events_relative Redis stream."""
    if not redis_pool:
        return

    try:
        # Use a separate non-decode connection for binary-safe xadd
        r = redis.from_url(REDIS_URL)
        await r.xadd(
            "speaker_events_relative",
            {
                "meeting_key": session.meeting_key,
                "meeting_id": str(session.meeting_id),
                "session_uid": session.session_uid,
                "source": "recall_backup",
                "event_type": event_type,
                "data": json.dumps(event_data),
            },
        )
        await r.aclose()
    except Exception as e:
        logger.error(f"[{session.meeting_key}] Error forwarding participant event: {e}")


# --- Health ---

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "recall_configured": bool(RECALL_API_KEY),
        "active_backup_sessions": len(active_sessions),
    }


# --- Management ---

@app.get("/recall/bots")
async def list_backup_bots():
    """List all active Recall backup sessions."""
    return {
        "active": {
            key: {
                "recall_bot_id": s.recall_bot_id,
                "meeting_id": s.meeting_id,
                "user_id": s.user_id,
                "platform": s.platform,
                "wl_ready": s.wl_ready,
                "queue_size": s.audio_queue.qsize(),
            }
            for key, s in active_sessions.items()
        }
    }


@app.delete("/recall/bots/{meeting_key}")
async def stop_backup_bot(meeting_key: str):
    """Stop a Recall backup bot."""
    session = active_sessions.get(meeting_key)
    if not session:
        return {"error": "No active backup session for this meeting"}

    try:
        await recall_client.leave_call(session.recall_bot_id)
    except Exception as e:
        logger.error(f"Error stopping Recall bot {session.recall_bot_id}: {e}")

    await cleanup_session(session)
    return {"status": "stopped", "recall_bot_id": session.recall_bot_id}
