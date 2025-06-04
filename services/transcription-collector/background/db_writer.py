import logging
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Set

import redis # For redis.exceptions
import redis.asyncio as aioredis

from shared_models.database import async_session_local
from shared_models.models import Transcription
# No schemas needed directly by these functions as they create Transcription objects
from config import BACKGROUND_TASK_INTERVAL, IMMUTABILITY_THRESHOLD
from filters import TranscriptionFilter

logger = logging.getLogger(__name__)

# This helper is used by process_redis_to_postgres
def create_transcription_object(meeting_id: int, start: float, end: float, text: str, language: Optional[str], session_uid: Optional[str]) -> Transcription:
    """Creates a Transcription ORM object without adding/committing."""
    return Transcription(
        meeting_id=meeting_id,
        start_time=start,
        end_time=end,
        text=text,
        language=language,
        session_uid=session_uid, 
        created_at=datetime.now(timezone.utc)
    )

async def process_redis_to_postgres(redis_c: aioredis.Redis, local_transcription_filter: TranscriptionFilter):
    """
    Background task that runs periodically to:
    1. Check for segments in Redis Hashes that are older than IMMUTABILITY_THRESHOLD
    2. Filter these segments
    3. Store passing segments in PostgreSQL 
    4. Remove processed segments from Redis Hashes
    """
    logger.info("Background Redis-to-PostgreSQL processor started")
    
    while True:
        try:
            await asyncio.sleep(BACKGROUND_TASK_INTERVAL)
            logger.debug("Background processor checking for immutable segments in Redis Hashes...")
            
            meeting_ids_raw = await redis_c.smembers("active_meetings")
            if not meeting_ids_raw:
                logger.debug("No active meetings found in Redis Set")
                continue
                
            meeting_ids = [mid for mid in meeting_ids_raw]
            logger.debug(f"Found {len(meeting_ids)} active meetings in Redis Set")
            
            batch_to_store = []
            segments_to_delete_from_redis: Dict[int, Set[str]] = {}  
            
            async with async_session_local() as db:
                for meeting_id_str in meeting_ids:
                    try:
                        meeting_id = int(meeting_id_str)
                        hash_key = f"meeting:{meeting_id}:segments"
                        redis_segments = await redis_c.hgetall(hash_key)
                        
                        if not redis_segments:
                            await redis_c.srem("active_meetings", meeting_id_str)
                            logger.debug(f"Removed empty meeting {meeting_id} from active meetings set")
                            continue
                            
                        logger.debug(f"Processing {len(redis_segments)} segments from Redis Hash for meeting {meeting_id}")
                        immutability_time = datetime.now(timezone.utc) - timedelta(seconds=IMMUTABILITY_THRESHOLD)
                        
                        for start_time_str, segment_json in redis_segments.items():
                            try:
                                segment_data = json.loads(segment_json)
                                segment_session_uid = segment_data.get("session_uid")
                                if 'updated_at' not in segment_data:
                                     logger.warning(f"Segment {start_time_str} in meeting {meeting_id} hash is missing 'updated_at'. Skipping immutability check.")
                                     continue 
                                
                                # Handle 'Z' suffix in timestamps
                                updated_at_str = segment_data['updated_at']
                                if updated_at_str.endswith('Z'):
                                    updated_at_str = updated_at_str[:-1] + '+00:00'
                                segment_updated_at = datetime.fromisoformat(updated_at_str)
                                if segment_updated_at.tzinfo is None: 
                                    segment_updated_at = segment_updated_at.replace(tzinfo=timezone.utc)
                                
                                if segment_updated_at < immutability_time:
                                    if local_transcription_filter.filter_segment(segment_data['text'], language=segment_data.get('language')):
                                        new_transcription = create_transcription_object(
                                            meeting_id=meeting_id,
                                            start=float(start_time_str),
                                            end=segment_data['end_time'],
                                            text=segment_data['text'],
                                            language=segment_data.get('language'),
                                            session_uid=segment_session_uid
                                        )
                                        batch_to_store.append(new_transcription)
                                    segments_to_delete_from_redis.setdefault(meeting_id, set()).add(start_time_str)
                            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                                logger.error(f"Error processing segment {start_time_str} from hash for meeting {meeting_id}: {e}")
                                segments_to_delete_from_redis.setdefault(meeting_id, set()).add(start_time_str)
                    except Exception as e:
                        logger.error(f"Error processing meeting {meeting_id_str} in Redis-to-PG task: {e}", exc_info=True)
                
                if batch_to_store:
                    try:
                        db.add_all(batch_to_store)
                        await db.commit()
                        logger.info(f"Stored {len(batch_to_store)} segments to PostgreSQL from {len(segments_to_delete_from_redis)} meetings")
                        
                        for meeting_id, start_times in segments_to_delete_from_redis.items():
                            if start_times:
                                hash_key = f"meeting:{meeting_id}:segments"
                                await redis_c.hdel(hash_key, *start_times)
                                logger.debug(f"Deleted {len(start_times)} processed segments for meeting {meeting_id} from Redis Hash")
                    except Exception as e:
                        logger.error(f"Error committing batch to PostgreSQL: {e}", exc_info=True)
                        await db.rollback()
                else:
                    logger.debug("No segments ready for PostgreSQL storage this interval.")
        
        except asyncio.CancelledError:
            logger.info("Redis-to-PostgreSQL processor task cancelled")
            break
        except redis.exceptions.ConnectionError as e:
             logger.error(f"Redis connection error in Redis-to-PG task: {e}. Retrying after delay...", exc_info=True)
             await asyncio.sleep(5) 
        except Exception as e:
            logger.error(f"Unhandled error in Redis-to-PostgreSQL processor: {e}", exc_info=True)
            await asyncio.sleep(BACKGROUND_TASK_INTERVAL) 