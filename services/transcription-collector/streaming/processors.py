import logging
import json
import uuid
import os
import hmac
import base64
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

import redis # For redis.exceptions
import redis.asyncio as aioredis # For type hinting redis_client
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
# from pydantic import ValidationError # Not explicitly used in the snippets for these functions, but could be for WhisperLiveData

from shared_models.database import async_session_local # For DB sessions
from shared_models.models import User, Meeting, MeetingSession, APIToken
from shared_models.schemas import Platform # WhisperLiveData not directly used by these functions from snippet
from config import REDIS_SEGMENT_TTL, REDIS_SPEAKER_EVENT_KEY_PREFIX, REDIS_SPEAKER_EVENT_TTL # Added new configs (NEW)
# MODIFIED: Import the new utility function and only necessary statuses/base mapper if still needed elsewhere
from mapping.speaker_mapper import get_speaker_mapping_for_segment, STATUS_UNKNOWN, STATUS_ERROR # Removed direct map_speaker_to_segment and other statuses if not directly used by this file

logger = logging.getLogger(__name__)

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _b64url_decode(data: str) -> bytes:
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def verify_meeting_token(token: str) -> Optional[dict]:
    try:
        if not token:
            return None
        secret = os.environ.get("ADMIN_TOKEN") or os.environ.get("ADMIN_API_TOKEN")
        if not secret:
            logger.error("ADMIN_TOKEN not set; cannot verify MeetingToken")
            return None
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header_b64, payload_b64, signature_b64 = parts
        header_json = _b64url_decode(header_b64)
        payload_json = _b64url_decode(payload_b64)
        header = json.loads(header_json)
        payload = json.loads(payload_json)
        if header.get('alg') != 'HS256' or header.get('typ') != 'JWT':
            return None
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = hmac.new(secret.encode("utf-8"), signing_input, digestmod='sha256').digest()
        expected_b64 = _b64url_encode(expected_sig)
        if not hmac.compare_digest(expected_b64, signature_b64):
            return None
        # Basic claims checks
        now = int(datetime.now(timezone.utc).timestamp())
        if 'exp' in payload and int(payload['exp']) < now:
            return None
        if payload.get('aud') != 'transcription-collector' or payload.get('iss') != 'bot-manager':
            return None
        if payload.get('scope') != 'transcribe:write':
            return None
        if 'meeting_id' not in payload:
            return None
        return payload
    except Exception as e:
        logger.warning(f"MeetingToken verification failed: {e}")
        return None

async def process_session_start_event(message_id: str, stream_data: Dict[str, Any], db: AsyncSession, user: Optional[User], meeting: Meeting, redis_c: aioredis.Redis) -> bool:
    """Processes a session_start event.
    
    Updates the MeetingSession database record with the accurate start time.
    Caches session start time in Redis for fast absolute timestamp computation.
    Uses pre-fetched user and meeting objects.
    
    Returns True if processing is considered complete (can be ACKed), 
    False if a potentially recoverable error occurred (should not be ACKed).
    """
    try:
        # 1. Validate required fields for session_start (token, platform, meeting_id already validated by caller)
        required_fields = ["uid", "start_timestamp"]
        if not all(field in stream_data for field in required_fields):
            logger.warning(f"Session start message {message_id} missing required fields for session processing. Skipping. Required: {required_fields}")
            return True  # Handled error, OK to ACK
        
        # 2. Parse the start timestamp
        start_timestamp_str = stream_data['start_timestamp']
        try:
            if start_timestamp_str.endswith('Z'):
                start_timestamp_str = start_timestamp_str[:-1]
            start_timestamp = datetime.fromisoformat(start_timestamp_str).replace(tzinfo=timezone.utc)
        except ValueError as e:
            logger.warning(f"Invalid timestamp format in session_start message {message_id}: {e}. Data: {start_timestamp_str}")
            return True  # Bad data, OK to ACK
        
        # 3. Update the meeting's session start time
        session_uid = stream_data['uid']
        stmt_session = select(MeetingSession).where(
            MeetingSession.meeting_id == meeting.id,
            MeetingSession.session_uid == session_uid
        )
        result_session = await db.execute(stmt_session)
        meeting_session = result_session.scalars().first()
        
        if meeting_session:
            meeting_session.session_start_time = start_timestamp
            logger.info(f"Updated start time for existing session {session_uid}, meeting_id {meeting.id} to {start_timestamp}")
        else:
            meeting_session = MeetingSession(
                meeting_id=meeting.id,
                session_uid=session_uid,
                session_start_time=start_timestamp
            )
            db.add(meeting_session)
            logger.info(f"Created new session {session_uid} for meeting_id {meeting.id} with start time {start_timestamp}")
        
        await db.commit()
        
        # 4. Cache session start time in Redis for fast lookup during transcription
        try:
            session_start_cache_key = f"meeting_session:{session_uid}:start"
            await redis_c.set(session_start_cache_key, start_timestamp.isoformat(), ex=7200)  # 2 hour TTL
            logger.info(f"Cached session start time in Redis: {session_start_cache_key}")
        except Exception as redis_err:
            logger.warning(f"Failed to cache session start time in Redis for session {session_uid}: {redis_err}")
        
        logger.info(f"Successfully processed session_start event for meeting {meeting.id}, session {session_uid}")
        return True

    except Exception as e:
        logger.error(f"Error processing session_start_event for message {message_id}, meeting {meeting.id if meeting else 'Unknown'}: {e}", exc_info=True)
        try:
            await db.rollback() # Rollback on error
        except Exception as rb_err:
            logger.error(f"Failed to rollback after error in process_session_start_event: {rb_err}", exc_info=True)
        return False # Unexpected error, DO NOT ACK

async def process_stream_message(message_id: str, message_data: Dict[str, Any], redis_c: aioredis.Redis) -> bool:
    """Processes a single message payload from the Redis stream.
    Returns True if processing is considered complete (can be ACKed), 
    False if a potentially recoverable error occurred (should not be ACKed).
    """
    payload_json = "" 
    try:
        if 'payload' not in message_data:
            logger.warning(f"Message {message_id} missing 'payload' field. Skipping.")
            return True 
        
        payload_json = message_data['payload']
        stream_data = json.loads(payload_json)
        message_type = stream_data.get("type", "transcription")
        
        user: Optional[User] = None
        meeting: Optional[Meeting] = None
        internal_meeting_id: Optional[int] = None

        async with async_session_local() as db:
            try:
                # Verify MeetingToken and extract claims
                token = stream_data.get('token')
                claims = verify_meeting_token(token)
                if not claims:
                    logger.warning(f"Message {message_id} (type: {message_type}) failed MeetingToken verification. Skipping.")
                    return True

                internal_meeting_id = int(claims.get('meeting_id'))
                platform_val = claims.get('platform') or stream_data.get('platform')
                native_meeting_id = claims.get('native_meeting_id') or stream_data.get('meeting_id')

                # Process different message types
                if message_type == "session_start":
                    # Fetch meeting by id for session creation (rare path; acceptable DB hit)
                    meeting = await db.get(Meeting, internal_meeting_id)
                    if not meeting:
                        logger.warning(f"Session start for unknown meeting_id {internal_meeting_id}. Skipping.")
                        return True
                    return await process_session_start_event(message_id, stream_data, db, None, meeting, redis_c) 
                elif message_type == "transcription":
                    pass # Continue with transcription processing
                elif message_type == "session_end": # NEW: Handle session_end for cleanup
                    session_uid = stream_data.get('uid')
                    if not session_uid:
                        logger.warning(f"Message {message_id} (type: session_end) missing 'uid'. Skipping cleanup.")
                        return True # Cannot process without UID, but ack
                    
                    speaker_event_key = f"{REDIS_SPEAKER_EVENT_KEY_PREFIX}:{session_uid}"
                    session_start_cache_key = f"meeting_session:{session_uid}:start"
                    try:
                        deleted_count = await redis_c.delete(speaker_event_key, session_start_cache_key)
                        logger.info(f"Processed session_end for UID '{session_uid}'. Deleted speaker events and session start cache from Redis (count: {deleted_count}).")
                        # Note: MeetingSession.session_end_utc is not updated here due to no DB model changes allowed.
                    except redis.exceptions.RedisError as e_redis:
                        logger.error(f"Redis error deleting keys for UID '{session_uid}' on session_end: {e_redis}")
                        return False # Retryable Redis error
                    return True # Successfully processed session_end
                else:
                    logger.warning(f"Message {message_id} has unknown type '{message_type}'. Skipping.")
                    return True

            except ValueError as ve: # Raised by get_user_by_token or other validation
                logger.warning(f"Auth/Lookup or validation failed for message {message_id}: {ve}. Skipping.")
                return True 
            except Exception as db_err:
                logger.error(f"DB/Lookup error preparing for message {message_id}: {db_err}", exc_info=True)
                await db.rollback()
                return False

            # --- Transcription type processing --- 
            required_fields_transcription = ["segments"]
            if not all(field in stream_data for field in required_fields_transcription):
                 logger.warning(f"Transcription message {message_id} payload missing 'segments' field. Skipping. Payload: {payload_json[:200]}...")
                 return True

            segment_count = 0
            hash_key = f"meeting:{internal_meeting_id}:segments"
            segments_to_store = {}
            changed_segments = []  # Track which segments actually changed
            session_uid_from_payload = stream_data.get('uid')
            # Resolve session start time for absolute UTC timestamp computation
            session_start_utc = None
            try:
                if session_uid_from_payload:
                    # Try Redis cache first for fast lookup
                    session_start_cache_key = f"meeting_session:{session_uid_from_payload}:start"
                    cached_start = await redis_c.get(session_start_cache_key)
                    if cached_start:
                        try:
                            cached_str = cached_start if isinstance(cached_start, str) else cached_start.decode('utf-8')
                            if cached_str.endswith('Z'):
                                cached_str = cached_str[:-1]
                            session_start_utc = datetime.fromisoformat(cached_str).replace(tzinfo=timezone.utc)
                            logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Loaded session start from Redis cache for UID {session_uid_from_payload}")
                        except Exception as cache_parse_err:
                            logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Failed to parse cached session start: {cache_parse_err}")
                    
                    # Fallback to DB if not in cache
                    if not session_start_utc:
                        stmt_session_time = select(MeetingSession).where(
                            MeetingSession.meeting_id == internal_meeting_id,
                            MeetingSession.session_uid == session_uid_from_payload
                        )
                        result_session_time = await db.execute(stmt_session_time)
                        session_row = result_session_time.scalars().first()
                        if session_row and getattr(session_row, 'session_start_time', None):
                            session_start_utc = session_row.session_start_time
                            # Cache it for next time
                            try:
                                await redis_c.set(session_start_cache_key, session_start_utc.isoformat(), ex=7200)
                                logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Loaded session start from DB and cached for UID {session_uid_from_payload}")
                            except Exception:
                                pass
            except Exception as _sess_err:
                logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Unable to resolve session start time for UID {session_uid_from_payload}: {_sess_err}")

            if not session_uid_from_payload:
                logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Message missing 'uid' for transcription segments. Cannot map speakers. Segments in this message will not have speaker info.")
            
            for i, segment in enumerate(stream_data.get('segments', [])):
                 if not isinstance(segment, dict) or segment.get('start') is None or segment.get('end') is None:
                     logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping segment {i} missing structure or 'start'/'end': {segment}")
                     continue
                 try:
                     start_time_float = float(segment['start'])
                     end_time_float = float(segment['end'])
                     text_content = segment.get('text') or ""
                     language_content = segment.get('language')
                     # WhisperLive provides this; we must propagate it so the UI can observe
                     # partial -> completed transitions (e.g., SAME_OUTPUT_THRESHOLD confirmation).
                     completed_content = bool(segment.get('completed', False))
                 except (ValueError, TypeError) as time_err:
                     logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping segment {i} invalid time format: {time_err} - Segment: {segment}")
                     continue
                
                 # Fix inverted timestamps
                 if end_time_float < start_time_float:
                     start_time_float, end_time_float = end_time_float, start_time_float
                     logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Corrected inverted times to start={start_time_float}, end={end_time_float}")
                
                 # Skip zero/negative duration segments
                 if end_time_float - start_time_float < 1e-3:
                     logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping ~zero-length segment: {segment}")
                     continue
                            
                 start_time_key = f"{start_time_float:.3f}"
                 
                 mapping_status: str = STATUS_UNKNOWN

                 if session_uid_from_payload:
                    # MODIFIED: Call the new utility function
                    context_log = f"[LiveMap Msg:{message_id}/Meet:{internal_meeting_id}/Seg:{start_time_key}]"
                    mapping_result = await get_speaker_mapping_for_segment(
                        redis_c=redis_c,
                        session_uid=session_uid_from_payload,
                        segment_start_ms=start_time_float * 1000,
                        segment_end_ms=end_time_float * 1000,
                        config_speaker_event_key_prefix=REDIS_SPEAKER_EVENT_KEY_PREFIX,
                        context_log_msg=context_log
                    )
                    mapped_speaker_name = mapping_result.get("speaker_name")
                    mapping_status = mapping_result.get("status", STATUS_ERROR) # Default to STATUS_ERROR if not present
                 else:
                    # This case is now handled inside get_speaker_mapping_for_segment if session_uid is None,
                    # but keeping explicit handling here is also fine for clarity if session_uid_from_payload is None from the start.
                    logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}/Seg {start_time_key}] No session_uid_from_payload. Cannot map speakers.")
                    mapping_status = STATUS_UNKNOWN

                 # Compute absolute UTC timestamps if session start time is known
                 abs_start_iso = None
                 abs_end_iso = None
                 if session_start_utc is not None:
                     try:
                         abs_start_dt = session_start_utc + timedelta(seconds=start_time_float)
                         abs_end_dt = session_start_utc + timedelta(seconds=end_time_float)
                         abs_start_iso = abs_start_dt.isoformat()
                         abs_end_iso = abs_end_dt.isoformat()
                     except Exception as _abs_err:
                         logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Failed to compute absolute times: {_abs_err}")
                 
                 segment_redis_data = {
                     "text": text_content,
                     "end_time": end_time_float,
                     "language": language_content,
                     "completed": completed_content,
                     "updated_at": datetime.now(timezone.utc).isoformat(), 
                     "session_uid": session_uid_from_payload,
                     "speaker": mapped_speaker_name,
                     "speaker_mapping_status": mapping_status
                 }
                 if abs_start_iso:
                     segment_redis_data["absolute_start_time"] = abs_start_iso
                 if abs_end_iso:
                     segment_redis_data["absolute_end_time"] = abs_end_iso
                 
                 # Change-only publishing: compare with existing segment
                 try:
                     existing_json = await redis_c.hget(hash_key, start_time_key)
                     if existing_json:
                         existing_data = json.loads(existing_json)
                         # Normalize fields for comparison (render-relevant only)
                         existing_norm = {
                             "text": existing_data.get("text"),
                             "speaker": existing_data.get("speaker"),
                             "language": existing_data.get("language"),
                             "completed": bool(existing_data.get("completed", False)),
                             "end_time": round(float(existing_data.get("end_time", 0)), 3),
                             "absolute_start_time": existing_data.get("absolute_start_time"),
                             "absolute_end_time": existing_data.get("absolute_end_time")
                         }
                         new_norm = {
                             "text": text_content,
                             "speaker": mapped_speaker_name,
                             "language": language_content,
                             "completed": completed_content,
                             "end_time": round(end_time_float, 3),
                             "absolute_start_time": abs_start_iso,
                             "absolute_end_time": abs_end_iso
                         }
                         if existing_norm == new_norm:
                            # No change; skip HSET and don't include in changed_segments
                            # BUT: Always store partial segments (completed=False) even if unchanged for GET endpoint consistency
                            if not completed_content:
                                # Partial segment: always store even if unchanged for GET endpoint
                                # Don't continue - fall through to store it
                                pass
                            else:
                                # Completed segment unchanged: skip storing
                                logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}/Seg {start_time_key}] Completed segment unchanged, skipping.")
                                continue
                 except Exception as _cmp_err:
                     logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Change comparison failed: {_cmp_err}; treating as changed")
                 
                 # Store and mark as changed
                 segments_to_store[start_time_key] = json.dumps(segment_redis_data)
                 segment_count += 1
                 changed_segments.append({
                     "start": start_time_float,
                     "text": text_content,
                     "end_time": end_time_float,
                     "language": language_content,
                     "completed": completed_content,
                     "speaker": mapped_speaker_name,
                     "session_uid": session_uid_from_payload,
                     "speaker_mapping_status": mapping_status,
                     "absolute_start_time": abs_start_iso,
                     "absolute_end_time": abs_end_iso
                 })
            
            if segment_count > 0:
                try:
                    async with redis_c.pipeline(transaction=True) as pipe:
                        pipe.sadd(f"active_meetings", str(internal_meeting_id))
                        pipe.expire(hash_key, REDIS_SEGMENT_TTL)
                        if segments_to_store:
                            pipe.hset(hash_key, mapping=segments_to_store)
                        results = await pipe.execute()
                        if any(res is None for res in results): # Simplified critical failure check
                            logger.error(f"Redis pipeline command failed critically for message {message_id}. Results: {results}")
                            return False
                        logger.info(f"Stored/Updated {segment_count} segments in Redis from message {message_id} for meeting {internal_meeting_id}. Results: {results}")
                except redis.exceptions.RedisError as redis_err:
                    logger.error(f"Redis pipeline error storing segments for message {message_id}: {redis_err}", exc_info=True)
                    return False 
                except Exception as pipe_err:
                     logger.error(f"Unexpected pipeline error storing segments for message {message_id}: {pipe_err}", exc_info=True)
                     return False
                
                # Publish mutable transcript update via Redis Pub/Sub (change-only)
                if changed_segments:
                    try:
                        event_payload = {
                            "type": "transcript.mutable",
                            "meeting": {"id": internal_meeting_id},
                            "payload": {"segments": changed_segments},
                            "ts": datetime.now(timezone.utc).isoformat()
                        }
                        channel = f"tc:meeting:{internal_meeting_id}:mutable"
                        await redis_c.publish(channel, json.dumps(event_payload))
                        logger.info(f"Published {len(changed_segments)} changed segments to {channel}")
                    except Exception as pub_err:
                        logger.error(f"Failed to publish mutable transcript update for meeting {internal_meeting_id}: {pub_err}")
                else:
                    logger.debug(f"No changed segments to publish for meeting {internal_meeting_id} from message {message_id}")
            else:
                logger.info(f"No valid segments found in message {message_id} for meeting {internal_meeting_id} to store in Redis.")
            return True

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON payload for message {message_id}: {e}. Payload: {payload_json[:200]}... Acking to avoid loop.")
        return True 
    except Exception as e:
        logger.error(f"Unexpected error in process_stream_message for {message_id}: {e}", exc_info=True)
        return False 

async def process_speaker_event_message(message_id: str, event_data: Dict[str, Any], redis_c: aioredis.Redis) -> bool:
    """Processes a single speaker event message from the Redis stream.
    Stores the event in a Redis Sorted Set keyed by session_uid.
    Returns True if processing is considered complete (can be ACKed),
    False if a potentially recoverable error occurred (should not be ACKed).
    """
    try:
        # Validate required fields for speaker event
        required_fields = ["uid", "relative_client_timestamp_ms", "event_type", "participant_name"]
        if not all(field in event_data for field in required_fields):
            logger.warning(f"[SpeakerProcessor] Speaker event message {message_id} missing required fields. Skipping. Data: {event_data}")
            return True  # Handled error (bad data), OK to ACK

        session_uid = event_data["uid"]
        try:
            # Ensure timestamp is a float for Redis score
            relative_timestamp_ms = float(event_data["relative_client_timestamp_ms"])
        except ValueError:
            logger.warning(f"[SpeakerProcessor] Invalid relative_client_timestamp_ms '{event_data['relative_client_timestamp_ms']}' for message {message_id}. Skipping.")
            return True # Bad data, OK to ACK

        # The entire event_data (which is the payload) will be stored as the value
        # Ensure it's JSON-serializable (it should be if it came from JSON stream)
        event_payload_json = json.dumps(event_data)
        
        sorted_set_key = f"{REDIS_SPEAKER_EVENT_KEY_PREFIX}:{session_uid}"

        async with redis_c.pipeline(transaction=True) as pipe:
            pipe.zadd(sorted_set_key, {event_payload_json: relative_timestamp_ms})
            pipe.expire(sorted_set_key, REDIS_SPEAKER_EVENT_TTL)
            results = await pipe.execute()

        # Check pipeline results (optional, zadd returns num added, expire returns 1 or 0)
        # For simplicity, we assume success if no exception
        logger.debug(f"[SpeakerProcessor] Stored speaker event for UID '{session_uid}' at {relative_timestamp_ms}ms. Key: {sorted_set_key}. Message ID: {message_id}")
        return True

    except json.JSONDecodeError as json_err: # Should not happen if data is already dict
        logger.error(f"[SpeakerProcessor] Error serializing speaker event payload to JSON for message {message_id}: {json_err}. Data: {event_data}")
        return True # Cannot process, but ack to avoid loop with bad data format.
    except redis.exceptions.RedisError as e_redis:
        logger.error(f"[SpeakerProcessor] Redis error processing speaker event message {message_id}: {e_redis}", exc_info=True)
        return False  # Potentially recoverable Redis error, DO NOT ACK
    except Exception as e:
        logger.error(f"[SpeakerProcessor] Unexpected error in process_speaker_event_message for {message_id}: {e}", exc_info=True)
        return False # Unexpected error, DO NOT ACK 