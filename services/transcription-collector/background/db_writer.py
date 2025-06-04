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
from config import BACKGROUND_TASK_INTERVAL, IMMUTABILITY_THRESHOLD, REDIS_SPEAKER_EVENT_KEY_PREFIX
from filters import TranscriptionFilter
# Speaker re-mapping before persistence
from mapping.speaker_mapper import (
    get_speaker_mapping_for_segment,
    STATUS_MAPPED,
    STATUS_UNKNOWN,
    STATUS_NO_SPEAKER_EVENTS,
    STATUS_MULTIPLE,
    STATUS_ERROR,
)

logger = logging.getLogger(__name__)

# This helper is used by process_redis_to_postgres
def create_transcription_object(meeting_id: int, start: float, end: float, text: str, language: Optional[str], session_uid: Optional[str], mapped_speaker_name: Optional[str]) -> Transcription:
    """Creates a Transcription ORM object without adding/committing."""
    return Transcription(
        meeting_id=meeting_id,
        start_time=start,
        end_time=end,
        text=text,
        speaker=mapped_speaker_name,
        language=language,
        session_uid=session_uid, 
        created_at=datetime.utcnow()
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
                        redis_segments_dict = await redis_c.hgetall(hash_key)
                        
                        if not redis_segments_dict:
                            await redis_c.srem("active_meetings", meeting_id_str)
                            local_transcription_filter.clear_processed_segments_cache(meeting_id)
                            logger.debug(f"Removed empty meeting {meeting_id} from active meetings set and cleared its filter cache.")
                            continue

                        sorted_segment_items = sorted(redis_segments_dict.items(), key=lambda item: float(item[0]))
                            
                        logger.debug(f"Processing {len(sorted_segment_items)} segments from Redis Hash for meeting {meeting_id} (sorted)")
                        immutability_time = datetime.now(timezone.utc) - timedelta(seconds=IMMUTABILITY_THRESHOLD)
                        
                        for start_time_str, segment_json in sorted_segment_items:
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
                                    # Segment is immutable. Attempt ONE FINAL speaker mapping pass if speaker name is missing or uncertain.
                                    mapped_speaker_name: Optional[str] = segment_data.get("speaker")
                                    mapping_status: str = segment_data.get("speaker_mapping_status", STATUS_UNKNOWN)

                                    needs_remap = (
                                        (not mapped_speaker_name)
                                        or mapping_status in (STATUS_UNKNOWN, STATUS_NO_SPEAKER_EVENTS, STATUS_ERROR)
                                    )

                                    if needs_remap and segment_session_uid:
                                        try:
                                            segment_start_ms = float(start_time_str) * 1000.0
                                            segment_end_ms = float(segment_data["end_time"]) * 1000.0
                                            
                                            context_log = f"[FinalMap Meet:{meeting_id}/Seg:{start_time_str}]"
                                            mapping_result = await get_speaker_mapping_for_segment(
                                                redis_c=redis_c,
                                                session_uid=segment_session_uid,
                                                segment_start_ms=segment_start_ms,
                                                segment_end_ms=segment_end_ms,
                                                config_speaker_event_key_prefix=REDIS_SPEAKER_EVENT_KEY_PREFIX,
                                                context_log_msg=context_log
                                            )

                                            mapped_speaker_name = mapping_result.get("speaker_name")
                                            mapping_status = mapping_result.get("status", STATUS_ERROR)
                                            
                                            # Persist new mapping back into Redis so API reflects it while still in Redis
                                            segment_data["speaker"] = mapped_speaker_name
                                            segment_data["speaker_mapping_status"] = mapping_status
                                            await redis_c.hset(hash_key, start_time_str, json.dumps(segment_data))

                                            logger.info(
                                                f"[FinalMap] Meeting {meeting_id} segment {start_time_str} remapped to '{mapped_speaker_name}' with status {mapping_status}"
                                            )
                                        except Exception as map_err:
                                            logger.error(
                                                f"[FinalMap] Error remapping speaker for meeting {meeting_id} segment {start_time_str}: {map_err}",
                                                exc_info=True,
                                            )

                                    else:
                                        logger.debug(
                                            f"Segment {start_time_str} (UID: {segment_session_uid}) uses speaker: '{mapped_speaker_name}' (status {mapping_status})"
                                        )

                                    # Filter the segment (deduplication, etc.)
                                    segment_start_time_float = float(start_time_str)
                                    segment_end_time_float = segment_data['end_time']
                                    
                                    if local_transcription_filter.filter_segment(
                                        segment_data['text'], 
                                        start_time=segment_start_time_float, 
                                        end_time=segment_end_time_float, 
                                        meeting_id=meeting_id,
                                        language=segment_data.get('language')
                                    ):
                                        new_transcription = create_transcription_object(
                                            meeting_id=meeting_id,
                                            start=segment_start_time_float,
                                            end=segment_end_time_float,
                                            text=segment_data['text'],
                                            language=segment_data.get('language'),
                                            session_uid=segment_session_uid,
                                            mapped_speaker_name=mapped_speaker_name
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