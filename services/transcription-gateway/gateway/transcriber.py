"""
AWS Transcribe Streaming: run session, push segments and speaker events to Redis.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict

import redis.asyncio as aioredis
from websockets.server import WebSocketServerProtocol

from gateway.client import get_redis
from gateway.settings import (
    AWS_REGION,
    DEFAULT_LANGUAGE,
    REDIS_SPEAKER_EVENTS_STREAM_NAME,
    REDIS_STREAM_NAME,
)
from gateway.utils import float32_to_pcm16, language_from_config

logger = logging.getLogger(__name__)


async def push_to_redis(redis: aioredis.Redis, payload: Dict[str, Any]) -> None:
    """Push a message to the transcription_segments stream (collector-compatible format)."""
    payload_json = json.dumps(payload)
    await redis.xadd(REDIS_STREAM_NAME, {"payload": payload_json}, maxlen=10000)
    logger.debug("Pushed to Redis stream %s", REDIS_STREAM_NAME)


async def push_speaker_event(redis: aioredis.Redis, event: Dict[str, Any]) -> None:
    """Push speaker event to speaker_events stream."""
    payload_json = json.dumps(event)
    await redis.xadd(REDIS_SPEAKER_EVENTS_STREAM_NAME, {"payload": payload_json}, maxlen=5000)
    logger.debug("Pushed speaker event to Redis")


async def run_aws_transcribe_session(
    ws: WebSocketServerProtocol,
    redis: aioredis.Redis,
    config: Dict[str, Any],
    audio_queue: asyncio.Queue,
    session_start_utc: datetime,
) -> None:
    """
    Run AWS Transcribe Streaming and push results to Redis.
    Bot sends Float32 16kHz; we convert to PCM 16-bit and send to AWS.
    """
    try:
        from amazon_transcribe.client import TranscribeStreamingClient
        from amazon_transcribe.handlers import TranscriptResultStreamHandler
        from amazon_transcribe.model import TranscriptEvent
    except ImportError:
        logger.debug("amazon-transcribe not installed. pip install amazon-transcribe")
        await ws.send(json.dumps({"status": "ERROR", "message": "AWS Transcribe not available"}))
        return

    uid = config.get("uid", "")
    token = config.get("token", "")
    platform = config.get("platform", "google_meet")
    meeting_id_native = config.get("meeting_id", "")
    language_code = language_from_config(config.get("language"), DEFAULT_LANGUAGE)

    class RedisHandler(TranscriptResultStreamHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._segment_offset_sec = 0.0

        async def handle_transcript_event(self, transcript_event: TranscriptEvent):
            results = transcript_event.transcript.results
            if not results:
                return
            segments = []
            for result in results:
                if not result.alternatives:
                    continue
                alt = result.alternatives[0]
                if not alt.transcript:
                    continue
                start = result.start_time if hasattr(result, "start_time") and result.start_time else 0
                end = result.end_time if hasattr(result, "end_time") and result.end_time else start + 1
                if hasattr(start, "seconds"):
                    start = start.seconds + start.nanos * 1e-9
                if hasattr(end, "seconds"):
                    end = end.seconds + end.nanos * 1e-9
                segments.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": alt.transcript.strip(),
                    "language": language_code.split("-")[0] if language_code else None,
                    "completed": not getattr(result, "is_partial", True),
                })
            if not segments:
                return
            payload = {
                "type": "transcription",
                "uid": uid,
                "token": token,
                "platform": platform,
                "meeting_id": meeting_id_native,
                "segments": segments,
            }
            await push_to_redis(redis, payload)
            logger.debug("Pushed %s segment(s) to Redis (AWS Transcribe returned text)", len(segments))

    try:
        client = TranscribeStreamingClient(region=AWS_REGION)
        stream = await client.start_stream_transcription(
            language_code=language_code,
            media_sample_rate_hz=16000,
            media_encoding="pcm",
        )

        async def write_audio():
            try:
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        break
                    pcm = float32_to_pcm16(chunk)
                    await stream.input_stream.send_audio_event(audio_chunk=pcm)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("write_audio error: %s", e)

        async def read_transcript():
            try:
                handler = RedisHandler(stream.output_stream)
                await handler.handle_events()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("read_transcript error: %s", e)

        await ws.send(json.dumps({"status": "SERVER_READY"}))
        writer = asyncio.create_task(write_audio())
        reader = asyncio.create_task(read_transcript())
        try:
            await asyncio.gather(writer, reader)
        finally:
            writer.cancel()
            reader.cancel()
            try:
                await stream.input_stream.end_stream()
            except Exception:
                pass

    except Exception as e:
        logger.debug("AWS Transcribe session error: %s", e)
        try:
            await ws.send(json.dumps({"status": "ERROR", "message": str(e)}))
        except Exception:
            pass
