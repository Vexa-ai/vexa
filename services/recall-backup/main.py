"""
Recall Backup Service

Listens for Vexa bot failures and automatically creates Recall.ai bots as fallback.
Receives real-time audio from Recall via WebSocket, pipes to WhisperLive for transcription.

Flow:
1. Bot Manager detects Vexa bot failure (fatal, meeting_not_found, timeout)
2. Publishes failure event to Redis: recall_backup:{meeting_id}
3. This service picks up the event, creates Recall bot via API
4. Recall bot joins meeting, streams audio_mixed_raw via WebSocket to our endpoint
5. We forward raw audio to WhisperLive WebSocket
6. Transcription flows through normal pipeline (Redis Streams → Collector → Customer)

The customer sees no difference — same /ws endpoint, same transcript format.
"""

import os
import json
import asyncio
import logging
from typing import Optional

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, WebSocket

logger = logging.getLogger(__name__)

app = FastAPI(title="Recall Backup Service")

# --- Config ---

RECALL_API_KEY = os.getenv("RECALL_API_KEY", "")
RECALL_BASE_URL = os.getenv("RECALL_BASE_URL", "https://us-west-2.recall.ai/api/v1")
RECALL_BOT_NAME = os.getenv("RECALL_BOT_NAME", "Meeting Assistant")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
WHISPERLIVE_URL = os.getenv("WHISPERLIVE_URL", "ws://whisperlive:9090/ws")

BOT_MANAGER_URL = os.getenv("BOT_MANAGER_URL", "http://bot-manager:8080")
CALLBACK_BASE_URL = os.getenv("CALLBACK_BASE_URL", "http://recall-backup:8090")

# --- State ---

# Active Recall bots: {vexa_meeting_key: recall_bot_id}
active_recall_bots: dict[str, str] = {}


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
        bot_name: str = RECALL_BOT_NAME,
        callback_url: Optional[str] = None,
    ) -> dict:
        """Create a Recall bot that streams raw audio back to us."""
        body = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "recording_config": {
                "transcript": {
                    # Use meeting captions (free) as backup transcript
                    "provider": {"meeting_captions": {}},
                },
                "realtime_endpoints": [],
                "participant_events": {},
            },
        }

        # If we have a callback URL, add raw audio streaming
        if callback_url:
            body["recording_config"]["realtime_endpoints"].append({
                "type": "webhook",
                "url": callback_url,
                "events": [
                    "audio_mixed_raw.data",
                    "transcript.data",
                    "transcript.partial_data",
                    "participant_events.join",
                    "participant_events.leave",
                ],
            })

        resp = await self.client.post("/bot/", json=body)
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


# --- Lifecycle ---

@app.on_event("startup")
async def startup():
    global recall_client
    if RECALL_API_KEY:
        recall_client = RecallClient(RECALL_API_KEY, RECALL_BASE_URL)
        logger.info("Recall backup service started with API key configured")
    else:
        logger.warning("RECALL_API_KEY not set — backup service disabled")

    # Start listening for bot failure events
    asyncio.create_task(listen_for_failures())


@app.on_event("shutdown")
async def shutdown():
    if recall_client:
        await recall_client.close()


# --- Redis Listener: Bot Failures ---

async def listen_for_failures():
    """Listen for Vexa bot failure events on Redis and trigger Recall backup."""
    r = redis.from_url(REDIS_URL)
    pubsub = r.pubsub()
    await pubsub.subscribe("vexa:bot:failure")

    logger.info("Listening for bot failure events on vexa:bot:failure")

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue

        try:
            data = json.loads(message["data"])
            meeting_url = data.get("meeting_url")
            meeting_key = data.get("meeting_key")  # platform:native_id
            user_id = data.get("user_id")
            failure_reason = data.get("reason")

            logger.info(
                f"Bot failure detected: {meeting_key} reason={failure_reason}"
            )

            if not recall_client or not meeting_url:
                continue

            # Don't double-backup
            if meeting_key in active_recall_bots:
                logger.info(f"Recall backup already active for {meeting_key}")
                continue

            # Create Recall bot as fallback
            callback_url = f"{CALLBACK_BASE_URL}/recall/events/{meeting_key}"
            bot = await recall_client.create_bot(
                meeting_url=meeting_url,
                callback_url=callback_url,
            )

            active_recall_bots[meeting_key] = bot["id"]
            logger.info(
                f"Recall backup bot created: {bot['id']} for {meeting_key}"
            )

            # Notify bot-manager that backup is active
            await r.publish(
                "vexa:bot:backup_active",
                json.dumps({
                    "meeting_key": meeting_key,
                    "recall_bot_id": bot["id"],
                    "user_id": user_id,
                }),
            )

        except Exception as e:
            logger.error(f"Error handling bot failure: {e}", exc_info=True)


# --- Webhook Receiver: Recall Real-Time Events ---

@app.post("/recall/events/{meeting_key}")
async def receive_recall_events(meeting_key: str, event: dict):
    """
    Receive real-time events from Recall bot.

    For audio_mixed_raw.data: forward raw audio to WhisperLive via Redis.
    For transcript/participant events: forward to Vexa's normal pipeline.
    """
    event_type = event.get("event", "")

    if event_type == "audio_mixed_raw.data":
        # Raw audio: base64 encoded 16kHz mono S16LE
        # Forward to WhisperLive via the same Redis stream Vexa bots use
        await forward_audio_to_whisperlive(meeting_key, event["data"])

    elif event_type in ("transcript.data", "transcript.partial_data"):
        # Recall's own transcript — can use as fallback or comparison
        await forward_transcript(meeting_key, event["data"])

    elif event_type.startswith("participant_events."):
        await forward_participant_event(meeting_key, event_type, event["data"])

    return {"status": "ok"}


async def forward_audio_to_whisperlive(meeting_key: str, audio_data: dict):
    """
    Forward raw audio from Recall to WhisperLive via Redis stream.

    The audio comes as base64-encoded 16kHz mono S16LE PCM.
    WhisperLive expects the same format via WebSocket.

    TODO: Implement WebSocket bridge to WhisperLive.
    For now, publish to Redis stream that WhisperLive consumer reads.
    """
    r = redis.from_url(REDIS_URL)
    await r.xadd(
        "recall_audio_stream",
        {
            "meeting_key": meeting_key,
            "audio_data": audio_data.get("data", ""),  # base64 PCM
            "timestamp": audio_data.get("timestamp", ""),
        },
    )
    await r.aclose()


async def forward_transcript(meeting_key: str, transcript_data: dict):
    """Forward Recall transcript to Vexa's transcription stream as backup."""
    r = redis.from_url(REDIS_URL)
    await r.xadd(
        "transcription_segments",
        {
            "meeting_key": meeting_key,
            "source": "recall_backup",
            "data": json.dumps(transcript_data),
        },
    )
    await r.aclose()


async def forward_participant_event(
    meeting_key: str, event_type: str, event_data: dict
):
    """Forward Recall participant events to Vexa's event stream."""
    r = redis.from_url(REDIS_URL)
    await r.xadd(
        "speaker_events_stream",
        {
            "meeting_key": meeting_key,
            "source": "recall_backup",
            "event_type": event_type,
            "data": json.dumps(event_data),
        },
    )
    await r.aclose()


# --- Health ---

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "recall_configured": bool(RECALL_API_KEY),
        "active_backup_bots": len(active_recall_bots),
    }


# --- Management ---

@app.get("/recall/bots")
async def list_backup_bots():
    """List all active Recall backup bots."""
    return {"active": active_recall_bots}


@app.delete("/recall/bots/{meeting_key}")
async def stop_backup_bot(meeting_key: str):
    """Stop a Recall backup bot."""
    bot_id = active_recall_bots.pop(meeting_key, None)
    if not bot_id:
        return {"error": "No active backup bot for this meeting"}

    try:
        await recall_client.leave_call(bot_id)
    except Exception as e:
        logger.error(f"Error stopping Recall bot {bot_id}: {e}")

    return {"status": "stopped", "recall_bot_id": bot_id}
