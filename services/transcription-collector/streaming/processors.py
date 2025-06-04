import logging
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import redis # For redis.exceptions
import redis.asyncio as aioredis # For type hinting redis_client
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
# from pydantic import ValidationError # Not explicitly used in the snippets for these functions, but could be for WhisperLiveData

from shared_models.database import async_session_local # For DB sessions
from shared_models.models import User, Meeting, MeetingSession, APIToken
from shared_models.schemas import Platform # WhisperLiveData not directly used by these functions from snippet
from config import REDIS_SEGMENT_TTL # Changed from ..config

logger = logging.getLogger(__name__)

async def get_user_by_token(token: str, db: AsyncSession) -> User:
    """Validates an API token and returns the associated User or raises ValueError."""
    if not token:
        raise ValueError("Missing API token") 
    
    result = await db.execute(
        select(User).join(APIToken).where(APIToken.token == token)
    )
    user = result.scalars().first()
    
    if not user:
        logger.warning(f"Invalid API token provided: {token[:5]}...")
        raise ValueError(f"Invalid API token") 
    return user

async def process_session_start_event(message_id: str, stream_data: Dict[str, Any], db: AsyncSession, user: User, meeting: Meeting) -> bool:
    """Processes a session_start event.
    
    Updates the MeetingSession database record with the accurate start time.
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
                # Common fields for both event types
                token = stream_data.get('token')
                platform_val = stream_data.get('platform')
                native_meeting_id = stream_data.get('meeting_id')

                if not all([token, platform_val, native_meeting_id]):
                    logger.warning(f"Message {message_id} (type: {message_type}) missing common required fields (token, platform, meeting_id). Skipping. Payload: {payload_json[:200]}...")
                    return True

                user = await get_user_by_token(token, db)
                
                stmt_meeting = select(Meeting).where(
                    Meeting.user_id == user.id,
                    Meeting.platform == platform_val,
                    Meeting.platform_specific_id == native_meeting_id
                ).order_by(Meeting.created_at.desc())
                result_meeting = await db.execute(stmt_meeting)
                meeting = result_meeting.scalars().first()

                if not meeting:
                    logger.warning(f"Meeting lookup failed for message {message_id}: No meeting found for user {user.id}, platform '{platform_val}', native ID '{native_meeting_id}'")
                    return True
                internal_meeting_id = meeting.id

                # Process different message types
                if message_type == "session_start":
                    return await process_session_start_event(message_id, stream_data, db, user, meeting) 
                elif message_type == "transcription":
                    pass # Continue with transcription processing
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
            
            for i, segment in enumerate(stream_data.get('segments', [])):
                 if not isinstance(segment, dict) or segment.get('start') is None or segment.get('end') is None:
                     logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping segment {i} missing basic structure or 'start'/'end' times: {segment}")
                     continue
                 try:
                     start_time_float = float(segment['start'])
                     end_time_float = float(segment['end'])
                     text_content = segment.get('text') or ""
                     language_content = segment.get('language')
                 except (ValueError, TypeError) as time_err:
                     logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping segment {i} with invalid time format: {time_err} - Segment: {segment}")
                     continue
                            
                 start_time_key = f"{start_time_float:.3f}"
                 session_uid_from_payload = stream_data.get('uid') 
                 segment_redis_data = {
                     "text": text_content,
                     "end_time": end_time_float,
                     "language": language_content,
                     "updated_at": datetime.now(timezone.utc).isoformat(), # Use timezone.utc
                     "session_uid": session_uid_from_payload 
                 }
                 segments_to_store[start_time_key] = json.dumps(segment_redis_data)
                 segment_count += 1
            
            if segment_count > 0:
                try:
                    async with redis_c.pipeline(transaction=True) as pipe:
                        pipe.sadd(f"active_meetings", str(internal_meeting_id)) # Ensure active_meetings key is just the string
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
            else:
                logger.info(f"No valid segments found in message {message_id} for meeting {internal_meeting_id} to store in Redis.")
            return True

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON payload for message {message_id}: {e}. Payload: {payload_json[:200]}... Acking to avoid loop.")
        return True 
    except Exception as e:
        logger.error(f"Unexpected error in process_stream_message for {message_id}: {e}", exc_info=True)
        return False 