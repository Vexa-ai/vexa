import logging
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Set

import redis # For redis.exceptions
import redis.asyncio as aioredis
from sqlalchemy import text as sql_text

from ..database import async_session_local
from ..models import Transcription, Meeting
from .config import BACKGROUND_TASK_INTERVAL, IMMUTABILITY_THRESHOLD

logger = logging.getLogger(__name__)

def create_transcription_object(meeting_id: int, start: float, end: float, text: str, language: Optional[str], session_uid: Optional[str], mapped_speaker_name: Optional[str], segment_id: Optional[str] = None) -> Transcription:
    """Creates a Transcription ORM object without adding/committing."""
    return Transcription(
        meeting_id=meeting_id,
        start_time=start,
        end_time=end,
        text=text,
        speaker=mapped_speaker_name,
        language=language,
        session_uid=session_uid,
        segment_id=segment_id,
        created_at=datetime.utcnow()
    )

async def process_redis_to_postgres(redis_c: aioredis.Redis, local_transcription_filter=None):
    """
    Background task: move immutable segments from Redis Hash to Postgres.
    No dedup — segment_id uniqueness handles it via UPSERT.
    """
    logger.info("Background Redis-to-PostgreSQL processor started")

    while True:
        try:
            await asyncio.sleep(BACKGROUND_TASK_INTERVAL)

            meeting_ids_raw = await redis_c.smembers("active_meetings")
            if not meeting_ids_raw:
                continue

            batch_to_store = []
            segments_to_delete: Dict[int, Set[str]] = {}

            async with async_session_local() as db:
                for meeting_id_str in meeting_ids_raw:
                    try:
                        meeting_id = int(meeting_id_str)
                        hash_key = f"meeting:{meeting_id}:segments"
                        redis_segments = await redis_c.hgetall(hash_key)

                        if not redis_segments:
                            await redis_c.srem("active_meetings", meeting_id_str)
                            continue

                        immutability_time = datetime.now(timezone.utc) - timedelta(seconds=IMMUTABILITY_THRESHOLD)

                        for seg_key, segment_json in redis_segments.items():
                            try:
                                segment_data = json.loads(segment_json)

                                if 'updated_at' not in segment_data:
                                    continue

                                updated_at_str = segment_data['updated_at']
                                if updated_at_str.endswith('Z'):
                                    updated_at_str = updated_at_str[:-1] + '+00:00'
                                segment_updated_at = datetime.fromisoformat(updated_at_str)
                                if segment_updated_at.tzinfo is None:
                                    segment_updated_at = segment_updated_at.replace(tzinfo=timezone.utc)

                                if segment_updated_at < immutability_time:
                                    start = float(segment_data.get("start_time", 0))
                                    end = float(segment_data.get("end_time", 0))
                                    if end < start:
                                        start, end = end, start

                                    text = segment_data.get('text', '')
                                    if not text.strip():
                                        segments_to_delete.setdefault(meeting_id, set()).add(seg_key)
                                        continue

                                    batch_to_store.append(create_transcription_object(
                                        meeting_id=meeting_id,
                                        start=start,
                                        end=end,
                                        text=text,
                                        language=segment_data.get('language'),
                                        session_uid=segment_data.get('session_uid'),
                                        mapped_speaker_name=segment_data.get('speaker'),
                                        segment_id=segment_data.get('segment_id'),
                                    ))
                                    segments_to_delete.setdefault(meeting_id, set()).add(seg_key)
                            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                                logger.error(f"Error processing segment {seg_key} for meeting {meeting_id}: {e}")
                                segments_to_delete.setdefault(meeting_id, set()).add(seg_key)
                    except Exception as e:
                        logger.error(f"Error processing meeting {meeting_id_str}: {e}", exc_info=True)

                if batch_to_store:
                    try:
                        # UPSERT: insert or update by (meeting_id, segment_id)
                        for t in batch_to_store:
                            if t.segment_id:
                                await db.execute(
                                    sql_text("""
                                        INSERT INTO transcriptions (meeting_id, start_time, end_time, text, speaker, language, session_uid, segment_id, created_at)
                                        VALUES (:mid, :start, :end, :text, :speaker, :lang, :uid, :segid, :created)
                                        ON CONFLICT (meeting_id, segment_id) WHERE segment_id IS NOT NULL
                                        DO UPDATE SET text = :text, speaker = :speaker, end_time = :end, created_at = :created
                                    """),
                                    {"mid": t.meeting_id, "start": t.start_time, "end": t.end_time,
                                     "text": t.text, "speaker": t.speaker, "lang": t.language,
                                     "uid": t.session_uid, "segid": t.segment_id, "created": t.created_at}
                                )
                            else:
                                # Legacy segments without segment_id — plain insert
                                db.add(t)
                        await db.commit()
                        logger.info(f"Stored {len(batch_to_store)} segments to PostgreSQL")

                        for meeting_id, seg_keys in segments_to_delete.items():
                            if seg_keys:
                                hash_key = f"meeting:{meeting_id}:segments"
                                await redis_c.hdel(hash_key, *seg_keys)
                    except Exception as e:
                        logger.error(f"Error committing to PostgreSQL: {e}", exc_info=True)
                        await db.rollback()

        except asyncio.CancelledError:
            logger.info("Redis-to-PostgreSQL processor task cancelled")
            break
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis connection error: {e}. Retrying...", exc_info=True)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unhandled error in Redis-to-PG: {e}", exc_info=True)
            await asyncio.sleep(BACKGROUND_TASK_INTERVAL)
