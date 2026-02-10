"""
WebSocket server: accept bot connections, dispatch to transcriber and Redis.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from gateway.client import get_redis
from gateway.settings import WS_HOST, WS_PORT
from gateway.transcriber import push_speaker_event, push_to_redis, run_aws_transcribe_session

logger = logging.getLogger(__name__)
PING_INTERVAL_SEC = 20
PING_TIMEOUT_SEC = 20
CLOSE_TIMEOUT_SEC = 5
MAX_MESSAGE_SIZE_BYTES = 2 ** 20 
INVALID_JSON_PREVIEW_LENGTH = 200

async def handle_client(ws: WebSocketServerProtocol) -> None:
    """Handle one bot WebSocket connection: config then audio; run AWS Transcribe and push to Redis."""
    redis = await get_redis()
    config: Optional[Dict[str, Any]] = None
    audio_queue: Optional[asyncio.Queue] = None
    transcribe_task: Optional[asyncio.Task] = None

    try:
        while True:
            try:
                raw = await ws.recv()
            except websockets.exceptions.ConnectionClosed:
                break

            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Invalid JSON from client: %s", raw[:INVALID_JSON_PREVIEW_LENGTH])
                    continue

                if "uid" in data and "token" in data:
                    config = data
                    logger.debug(
                        "Config received: uid=%s, meeting_id=%s, platform=%s",
                        config.get("uid", ""),
                        config.get("meeting_id", ""),
                        config.get("platform", ""),
                    )
                    session_start_utc = datetime.now(timezone.utc)
                    audio_queue = asyncio.Queue()
                    try:
                        await push_to_redis(redis, {
                            "type": "session_start",
                            "uid": config.get("uid"),
                            "token": config.get("token"),
                            "platform": config.get("platform"),
                            "meeting_id": config.get("meeting_id"),
                            "start_timestamp": session_start_utc.isoformat(),
                        })
                    except Exception as e:
                        logger.debug("Failed to push session_start: %s", e)
                    transcribe_task = asyncio.create_task(
                        run_aws_transcribe_session(ws, redis, config, audio_queue, session_start_utc)
                    )
                    continue

                if data.get("type") == "speaker_activity" and config:
                    try:
                        await push_speaker_event(redis, data.get("payload", data))
                    except Exception as e:
                        logger.debug("Failed to push speaker event: %s", e)
                    continue

                if data.get("type") == "session_control" and config:
                    if data.get("payload", {}).get("event") == "LEAVING_MEETING":
                        try:
                            await push_to_redis(redis, {
                                "type": "session_end",
                                "uid": config.get("uid"),
                                "token": config.get("token"),
                                "platform": config.get("platform"),
                                "meeting_id": config.get("meeting_id"),
                            })
                        except Exception as e:
                            logger.debug("Failed to push session_end: %s", e)
                    continue

            elif isinstance(raw, bytes) and audio_queue is not None:
                try:
                    audio_queue.put_nowait(raw)
                except asyncio.QueueFull:
                    pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if audio_queue is not None:
            try:
                audio_queue.put_nowait(None)
            except Exception:
                pass
        if transcribe_task is not None:
            transcribe_task.cancel()
            try:
                await transcribe_task
            except asyncio.CancelledError:
                pass
        await redis.aclose()


async def main() -> None:
    async with websockets.serve(
        handle_client,
        WS_HOST,
        WS_PORT,
        ping_interval=PING_INTERVAL_SEC,
        ping_timeout=PING_TIMEOUT_SEC,
        close_timeout=CLOSE_TIMEOUT_SEC,
        max_size=MAX_MESSAGE_SIZE_BYTES,
    ):
        logger.debug("Transcription gateway (AWS Transcribe) listening on ws://%s:%s", WS_HOST, WS_PORT)
        await asyncio.Future()
