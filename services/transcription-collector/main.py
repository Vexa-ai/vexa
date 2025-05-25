import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Depends, Header, Security, status
import json
import logging
import uuid
import os
import asyncio
from datetime import datetime, timedelta, timezone
import redis # Import base redis package for exceptions
import redis.asyncio as aioredis # Use alias for async client
from sqlalchemy import select, and_, func, distinct, text
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from typing import Optional, List, Dict, Any, Set, Tuple
from pydantic import ValidationError

from shared_models.database import get_db, init_db, async_session_local
from shared_models.models import APIToken, User, Meeting, Transcription, MeetingSession
from shared_models.schemas import (
    TranscriptionSegment, 
    HealthResponse, 
    ErrorResponse,
    MeetingResponse,
    MeetingListResponse,
    TranscriptionResponse,
    Platform,
    WhisperLiveData # Keep for reference if needed for segment structure, but not for parsing input
)
from filters import TranscriptionFilter

# Configuration for Redis Stream consumer
REDIS_STREAM_NAME = os.environ.get("REDIS_STREAM_NAME", "transcription_segments")
REDIS_CONSUMER_GROUP = os.environ.get("REDIS_CONSUMER_GROUP", "collector_group")
REDIS_STREAM_READ_COUNT = int(os.environ.get("REDIS_STREAM_READ_COUNT", "10"))
REDIS_STREAM_BLOCK_MS = int(os.environ.get("REDIS_STREAM_BLOCK_MS", "2000")) # 2 seconds
# Use a fixed consumer name, potentially add hostname later if scaling replicas
CONSUMER_NAME = os.environ.get("POD_NAME", "collector-main") # Get POD_NAME from env if avail (k8s), else fixed
PENDING_MSG_TIMEOUT_MS = 60000 # Milliseconds: Timeout after which pending messages are considered stale (e.g., 1 minute)

# Configuration for background processing
BACKGROUND_TASK_INTERVAL = int(os.environ.get("BACKGROUND_TASK_INTERVAL", "10"))  # seconds
IMMUTABILITY_THRESHOLD = int(os.environ.get("IMMUTABILITY_THRESHOLD", "30"))  # seconds
REDIS_SEGMENT_TTL = int(os.environ.get("REDIS_SEGMENT_TTL", "3600"))  # 1 hour default TTL for Redis segments

app = FastAPI(
    title="Transcription Collector",
    description="Collects and stores transcriptions from WhisperLive instances via Redis Streams." # Updated description
)

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("transcription_collector")

# Security - API Key auth (used for /meetings and /transcripts endpoints)
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_current_user(api_key: str = Security(api_key_header),
                           db: AsyncSession = Depends(get_db)) -> User:
    """Dependency to verify X-API-Key and return the associated User."""
    if not api_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing API token")
    
    # Find the token in the database
    result = await db.execute(
        select(APIToken, User)
        .join(User, APIToken.user_id == User.id)
        .where(APIToken.token == api_key)
    )
    token_user = result.first()
    
    if not token_user:
        logger.warning(f"Invalid API token provided: {api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API token"
        )
    
    _token_obj, user_obj = token_user
    return user_obj

# Redis connection
redis_client: Optional[aioredis.Redis] = None # Use alias in type hint

# Initialize transcription filter
transcription_filter = TranscriptionFilter()

# Background task references
redis_to_pg_task = None
stream_consumer_task = None # New task reference

@app.on_event("startup")
async def startup():
    global redis_client, redis_to_pg_task, stream_consumer_task
    
    # Initialize Redis connection
    redis_host = os.environ.get("REDIS_HOST", "redis")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    logger.info(f"Connecting to Redis at {redis_host}:{redis_port}")
    
    redis_client = aioredis.Redis(
        host=redis_host,
        port=redis_port,
        db=0,
        decode_responses=True
    )
    await redis_client.ping()
    logger.info("Redis connection successful.")
    
    # Ensure Redis Stream Consumer Group exists
    try:
        logger.info(f"Ensuring Redis Stream group '{REDIS_CONSUMER_GROUP}' exists for stream '{REDIS_STREAM_NAME}'...")
        await redis_client.xgroup_create(
            name=REDIS_STREAM_NAME, 
            groupname=REDIS_CONSUMER_GROUP, 
            id='0',
            mkstream=True
        )
        logger.info(f"Consumer group '{REDIS_CONSUMER_GROUP}' ensured for stream '{REDIS_STREAM_NAME}'.")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP Consumer Group name already exists" in str(e):
            logger.info(f"Consumer group '{REDIS_CONSUMER_GROUP}' already exists for stream '{REDIS_STREAM_NAME}'.")
        else:
            logger.error(f"Failed to create Redis consumer group: {e}", exc_info=True)
            # Consider exiting if group creation fails unexpectedly
            return
    
    # Initialize database connection
    logger.info("Database initialized.")
    
    # Claim stale pending messages before starting main loop
    await claim_stale_messages()
    
    # Start background processing tasks
    redis_to_pg_task = asyncio.create_task(process_redis_to_postgres())
    logger.info(f"Redis-to-PostgreSQL task started (Interval: {BACKGROUND_TASK_INTERVAL}s, Threshold: {IMMUTABILITY_THRESHOLD}s)")
    
    stream_consumer_task = asyncio.create_task(consume_redis_stream())
    logger.info(f"Redis Stream consumer task started (Stream: {REDIS_STREAM_NAME}, Group: {REDIS_CONSUMER_GROUP}, Consumer: {CONSUMER_NAME})")

@app.on_event("shutdown")
async def shutdown():
    logger.info("Application shutting down...")
    # Cancel background tasks
    tasks_to_cancel = [redis_to_pg_task, stream_consumer_task]
    for i, task in enumerate(tasks_to_cancel):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Background task {i+1} cancelled.")
            except Exception as e:
                logger.error(f"Error during background task {i+1} cancellation: {e}", exc_info=True)
    
    # Close Redis connection
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed.")
    
    logger.info("Shutdown complete.")

# --- Helper function for processing a single stream message ---
async def process_stream_message(message_id: str, message_data: Dict[str, Any]) -> bool:
    """Processes a single message payload from the Redis stream.
    
    Returns True if processing is considered complete (can be ACKed), 
    False if a potentially recoverable error occurred (should not be ACKed).
    """
    payload_json = "" # Initialize for logging in case of early error
    try:
        # 1. Extract and parse JSON payload
        if 'payload' not in message_data:
            logger.warning(f"Message {message_id} missing 'payload' field. Skipping.")
            return True # Handled error, OK to ACK
        
        payload_json = message_data['payload']
        stream_data = json.loads(payload_json)

        # Check message type to determine how to process
        message_type = stream_data.get("type", "transcription")  # Default to transcription for backward compatibility
        
        # Process different message types
        if message_type == "session_start":
            return await process_session_start_event(message_id, stream_data)
        elif message_type == "transcription":
            # Continue with normal transcription processing below
            pass
        else:
            logger.warning(f"Message {message_id} has unknown type '{message_type}'. Skipping.")
            return True  # Unknown type, OK to ACK
            
        # 2. Validate required fields
        required_fields = ["platform", "meeting_id", "token", "segments"]
        if not all(field in stream_data for field in required_fields):
             logger.warning(f"Message {message_id} payload missing required fields. Skipping. Payload: {payload_json[:200]}...")
             return True # Handled error, OK to ACK

        # 3. Validate Token, Get User, Find Meeting ID
        user: Optional[User] = None
        internal_meeting_id: Optional[int] = None
        async with async_session_local() as db:
            try:
                # Use helper to get user, raises ValueError on failure
                user = await get_user_by_token(stream_data['token'], db)
                
                # Find meeting
                stmt_meeting = select(Meeting).where(
                    Meeting.user_id == user.id,
                    Meeting.platform == stream_data['platform'],
                    Meeting.platform_specific_id == stream_data['meeting_id']
                ).order_by(Meeting.created_at.desc())
                result_meeting = await db.execute(stmt_meeting)
                meeting = result_meeting.scalars().first()

                if not meeting:
                    logger.warning(f"Meeting lookup failed for message {message_id}: No meeting found for user {user.id}, platform '{stream_data['platform']}', native ID '{stream_data['meeting_id']}'")
                    return True # Persistent data issue, OK to ACK

                internal_meeting_id = meeting.id

            except ValueError as ve:
                # Specific errors from token/meeting lookup
                logger.warning(f"Auth/Lookup failed for message {message_id}: {ve}. Skipping.")
                return True # Persistent data issue, OK to ACK
            except Exception as db_err:
                # Generic DB or other errors during lookup phase
                logger.error(f"DB/Lookup error processing message {message_id}: {db_err}", exc_info=True)
                return False # Potentially recoverable DB error, DO NOT ACK

        # Ensure we have a meeting ID (should be guaranteed if we passed checks)
        if not internal_meeting_id:
             logger.error(f"Logic error: internal_meeting_id not found for message {message_id} after checks.")
             return True # Treat as handled error to avoid loops

        # Get speaker information from stream data if available
        speaker_id = stream_data.get('speaker_id')
        speaker_name = stream_data.get('speaker_name')
        
        # Log if we have speaker info
        if speaker_id or speaker_name:
            logger.info(f"[Msg {message_id}/Meet {internal_meeting_id}] Processing segments with speaker info: ID={speaker_id}, Name={speaker_name}")
        else:
            logger.info(f"[Msg {message_id}/Meet {internal_meeting_id}] Processing segments without speaker info.")

        # 4. Prepare segments for Redis Hash storage
        segment_count = 0
        hash_key = f"meeting:{internal_meeting_id}:segments"
        segments_to_store = {}
        logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Preparing to process {len(stream_data.get('segments', []))} raw segments.")

        for i, segment in enumerate(stream_data.get('segments', [])):
             logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Processing raw segment {i}: {segment}")
             # Minimal validation: check for 'start' and 'end' keys
             if not isinstance(segment, dict) or segment.get('start') is None or segment.get('end') is None:
                 logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping segment {i} missing basic structure or 'start'/'end' times: {segment}")
                 continue
             
             try:
                 # Validate and convert times using 'start' and 'end' keys
                 start_time_float = float(segment['start'])
                 end_time_float = float(segment['end'])
                 
                 # Get text, default to empty string if missing or None
                 text_content = segment.get('text') or ""
                 # Get language, default to None if missing
                 language_content = segment.get('language')

             except (ValueError, TypeError) as time_err:
                 logger.warning(f"[Msg {message_id}/Meet {internal_meeting_id}] Skipping segment {i} with invalid time format: {time_err} - Segment: {segment}")
                 continue
                        
             # Create data for Redis Hash
             start_time_key = f"{start_time_float:.3f}"
             session_uid_from_payload = stream_data.get('uid') # Get uid from message payload
             segment_redis_data = {
                 "text": text_content,
                 "end_time": end_time_float,
                 "language": language_content,
                 "updated_at": datetime.utcnow().isoformat() + "Z",
                 "session_uid": session_uid_from_payload, # Add session_uid
                 "speaker_id": speaker_id,  # Add speaker_id to segment data
                 "speaker_name": speaker_name  # Add speaker_name to segment data
             }
             segments_to_store[start_time_key] = json.dumps(segment_redis_data)
             segment_count += 1
             logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Prepared valid segment {i} for storage (key: {start_time_key})")
        
        logger.debug(f"[Msg {message_id}/Meet {internal_meeting_id}] Finished processing segments. Count to store: {segment_count}. Keys: {list(segments_to_store.keys())}")
        # 5. Update Redis (SADD, EXPIRE, HSET)
        if segment_count > 0:
            try:
                async with redis_client.pipeline(transaction=True) as pipe:
                    pipe.sadd("active_meetings", str(internal_meeting_id))
                    pipe.expire(hash_key, REDIS_SEGMENT_TTL)
                    if segments_to_store:
                        pipe.hset(hash_key, mapping=segments_to_store)
                    results = await pipe.execute()
                    # Check results: SADD returns int (0 or 1), EXPIRE returns bool/int (0/False if key missing), HSET returns int (0 or 1).
                    # We consider it a failure ONLY if a command returned None, indicating a more severe error than just EXPIRE on a non-existent key.
                    if any(res is None for res in results):
                         sadd_failed = results[0] is None
                         expire_failed = results[1] is None
                         hset_failed = len(results) > 2 and results[2] is None

                         error_details = []
                         if sadd_failed: error_details.append("SADD failed (returned None)")
                         if expire_failed: error_details.append("EXPIRE failed (returned None)")
                         if hset_failed: error_details.append("HSET failed (returned None)")

                         # Log error only if a command actually returned None
                         if error_details:
                              logger.error(f"Redis pipeline command failed critically for message {message_id}. Details: {', '.join(error_details)}. Results: {results}")
                              return False # Redis command failed critically, DO NOT ACK
                         else:
                              # This case should not be reached if the outer if is true, but safety first.
                              logger.warning(f"Pipeline check resulted in unexpected state (None detected but no specific command failed?) for message {message_id}. Results: {results}. Proceeding to ACK.")
                              return True # Proceed to ACK

                    # Log success (including cases where EXPIRE might have returned False/0)
                    logger.info(f"Stored/Updated {segment_count} segments in Redis from message {message_id}" + 
                              (f" with speaker {speaker_name}" if speaker_name else "") + 
                              f". Results: {results}")

                # Note: The duplicate logger.info line below was removed as it's now covered by the else block above.
                # logger.info(f"Stored {segment_count} segments in Redis from message {message_id} for meeting {internal_meeting_id}")
            except redis.exceptions.RedisError as redis_err: # Catch specific redis errors
                logger.error(f"Redis pipeline error storing segments for message {message_id}: {redis_err}", exc_info=True)
                return False # Potentially recoverable Redis error, DO NOT ACK
            except Exception as pipe_err: # Catch other potential errors during pipeline
                 logger.error(f"Unexpected pipeline error storing segments for message {message_id}: {pipe_err}", exc_info=True)
                 return False # Unexpected error, DO NOT ACK
        else:
            logger.info(f"No valid segments found in message {message_id} for meeting {internal_meeting_id}")

        # If we reach here without returning False, processing was successful
        return True

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON payload for message {message_id}: {e}. Payload: {payload_json[:200]}... Acking to avoid loop.")
        return True # Persistent data issue, OK to ACK
    except Exception as e:
        # Catch-all for unexpected errors during processing logic itself
        logger.error(f"Unexpected error in process_stream_message for {message_id}: {e}", exc_info=True)
        return False # Unexpected error, DO NOT ACK

# --- Helper function for processing session_start events ---
async def process_session_start_event(message_id: str, stream_data: Dict[str, Any]) -> bool:
    """Processes a session_start event from the Redis stream.
    
    Updates the MeetingSession database record with the accurate start time.
    
    Returns True if processing is considered complete (can be ACKed), 
    False if a potentially recoverable error occurred (should not be ACKed).
    """
    try:
        # 1. Validate required fields for session_start
        required_fields = ["platform", "meeting_id", "token", "uid", "start_timestamp"]
        if not all(field in stream_data for field in required_fields):
            logger.warning(f"Session start message {message_id} missing required fields. Skipping. Required: {required_fields}")
            return True  # Handled error, OK to ACK
            
        # 2. Validate Token, Get User, Find Meeting ID
        user: Optional[User] = None
        meeting: Optional[Meeting] = None
        
        async with async_session_local() as db:
            try:
                # Use helper to get user, raises ValueError on failure
                user = await get_user_by_token(stream_data['token'], db)
                
                # Find meeting
                stmt_meeting = select(Meeting).where(
                    Meeting.user_id == user.id,
                    Meeting.platform == stream_data['platform'],
                    Meeting.platform_specific_id == stream_data['meeting_id']
                ).order_by(Meeting.created_at.desc())
                result_meeting = await db.execute(stmt_meeting)
                meeting = result_meeting.scalars().first()

                if not meeting:
                    logger.warning(f"Meeting lookup failed for session_start message {message_id}: "
                                  f"No meeting found for user {user.id}, platform '{stream_data['platform']}', "
                                  f"native ID '{stream_data['meeting_id']}'")
                    return True  # Persistent data issue, OK to ACK
                
                # 3. Parse the start timestamp
                start_timestamp_str = stream_data['start_timestamp']
                try:
                    # Parse ISO format timestamp
                    if start_timestamp_str.endswith('Z'):
                        # Remove Z and handle as UTC
                        start_timestamp_str = start_timestamp_str[:-1]
                    start_timestamp = datetime.fromisoformat(start_timestamp_str)
                except ValueError as e:
                    logger.warning(f"Invalid timestamp format in session_start message {message_id}: {e}")
                    return True  # Bad data, OK to ACK
                
                # 4. Update the meeting's session start time
                # Find or create a MeetingSession record for this session
                session_uid = stream_data['uid']
                stmt_session = select(MeetingSession).where(
                    MeetingSession.meeting_id == meeting.id,
                    MeetingSession.session_uid == session_uid
                )
                result_session = await db.execute(stmt_session)
                meeting_session = result_session.scalars().first()
                
                if meeting_session:
                    # Update existing session
                    meeting_session.session_start_time = start_timestamp
                    logger.info(f"Updated start time for existing session {session_uid}, "
                              f"meeting_id {meeting.id} to {start_timestamp}")
                else:
                    # Create new session
                    meeting_session = MeetingSession(
                        meeting_id=meeting.id,
                        session_uid=session_uid,
                        session_start_time=start_timestamp
                    )
                    db.add(meeting_session)
                    logger.info(f"Created new session {session_uid} for meeting_id {meeting.id} "
                              f"with start time {start_timestamp}")
                
                # Commit the changes
                await db.commit()
                
                logger.info(f"Successfully processed session_start event for meeting {meeting.id}, "
                          f"session {session_uid}, start time: {start_timestamp}")
                return True
                
            except ValueError as ve:
                # Specific errors from token/meeting lookup
                logger.warning(f"Auth/Lookup failed for session_start message {message_id}: {ve}. Skipping.")
                return True  # Persistent data issue, OK to ACK
            except Exception as db_err:
                # Generic DB or other errors during lookup phase
                logger.error(f"DB/Lookup error processing session_start message {message_id}: {db_err}", exc_info=True)
                return False  # Potentially recoverable DB error, DO NOT ACK
    
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(f"Unexpected error in process_session_start_event for {message_id}: {e}", exc_info=True)
        return False  # Unexpected error, DO NOT ACK

# --- Function to claim and process stale messages ---
async def claim_stale_messages():
    claim_start_id = '0-0' # Start claiming from the beginning of time
    messages_claimed_total = 0
    processed_claim_count = 0
    acked_claim_count = 0
    error_claim_count = 0

    logger.info(f"Starting stale message check (idle > {PENDING_MSG_TIMEOUT_MS}ms).")

    try:
        while True:
            # Claim a batch of potentially stale messages for THIS consumer
            # XCLAIM <key> <group> <consumer> <min-idle-time> <ID-1> <ID-2> ... [FORCE] [JUSTID]
            # We claim IDs starting from claim_start_id
            # Note: xclaim itself returns only the messages successfully claimed.
            #       It needs message IDs to attempt claiming. We provide '0-0' initially
            #       and then the ID of the last claimed message on subsequent calls
            #       if we were looping (but we'll use xpending_range first).

            # Let's rethink: XCLAIM needs specific IDs. The easier approach is:
            # 1. Find *all* pending messages (like the original code started to do).
            # 2. Filter those by idle time.
            # 3. Attempt to XCLAIM the filtered IDs.

            # 1. Get detailed list of *all* pending messages for the group
            pending_details = await redis_client.xpending_range(
                name=REDIS_STREAM_NAME,
                groupname=REDIS_CONSUMER_GROUP,
                min='-', # Start of stream
                max='+', # End of stream
                count=100 # Process in batches
            )

            if not pending_details:
                logger.info("No more pending messages found for the group.")
                break # Exit the while loop

            # 2. Filter by idle time
            stale_candidates = [
                msg for msg in pending_details
                if msg.get('idle', 0) > PENDING_MSG_TIMEOUT_MS
            ]

            if not stale_candidates:
                logger.info("No messages found exceeding idle time in the current pending batch.")
                # If pending_details had messages but none were stale enough,
                # it implies we've checked all relevant messages for staleness.
                # (Assuming messages are processed roughly in order).
                # If pending_details was less than count=100, we're definitely done.
                if len(pending_details) < 100:
                    break
                else:
                    # There might be more pending messages, continue check from last seen ID?
                    # This logic gets complex. Let's stick to a simpler approach for now:
                    # Claim messages explicitly idle > threshold using XAUTOCLAIM if available,
                    # otherwise fall back to XPENDING + XCLAIM.
                    # Assuming XAUTOCLAIM is not used/available based on prior errors:
                    # We'll just check the first batch of 100. If more complex handling is needed,
                    # it requires careful pagination with xpending_range.
                    logger.warning("Checked 100 pending messages, none were stale. More might exist but stopping check for this run.")
                    break


            stale_message_ids = [msg['message_id'] for msg in stale_candidates]
            logger.info(f"Found {len(stale_message_ids)} potentially stale message(s) in pending list: {stale_message_ids}")

            # 3. Attempt to XCLAIM the stale message IDs for *this* consumer
            if stale_message_ids:
                # xclaim(stream, group, consumer, min_idle_time, message_ids)
                claimed_messages = await redis_client.xclaim(
                    name=REDIS_STREAM_NAME,
                    groupname=REDIS_CONSUMER_GROUP,
                    consumername=CONSUMER_NAME,
                    min_idle_time=PENDING_MSG_TIMEOUT_MS, # Ensure they are still stale
                    message_ids=stale_message_ids,
                )
                # claimed_messages format: [[message_id, {field: value, ...}], ...] or [] if none claimed

                messages_claimed_now = len(claimed_messages)
                messages_claimed_total += messages_claimed_now
                logger.info(f"Successfully claimed {messages_claimed_now} stale message(s): {[msg[0] for msg in claimed_messages]}")

                # 4. Process the claimed messages
                for message_id_bytes, message_data_bytes in claimed_messages:
                    message_id = message_id_bytes.decode('utf-8')
                    # Decode message data from bytes
                    message_data = {k.decode('utf-8'): v.decode('utf-8') for k, v in message_data_bytes.items()}
                    logger.info(f"Processing claimed stale message {message_id}...")
                    processed_claim_count += 1
                    try:
                        success = await process_stream_message(message_id, message_data)
                        if success:
                            logger.info(f"Successfully processed claimed stale message {message_id}. Acknowledging.")
                            await redis_client.xack(REDIS_STREAM_NAME, REDIS_CONSUMER_GROUP, message_id)
                            acked_claim_count += 1
                        else:
                            # Processing failed, don't ACK, it might be claimed again later
                            logger.warning(f"Processing failed for claimed stale message {message_id}. Not acknowledging.")
                            error_claim_count += 1
                    except Exception as e:
                        logger.error(f"Error processing claimed stale message {message_id}: {e}", exc_info=True)
                        error_claim_count += 1
                        # Don't ACK on unexpected error

            # If we claimed messages, or if we didn't find any stale ones in a full batch, break.
            # This simplifies logic to avoid infinite loops if xpending_range pagination isn't fully handled.
            break

    except redis.exceptions.RedisError as e:
        logger.error(f"Redis error during stale message claiming: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error during stale message claiming: {e}", exc_info=True)

    logger.info(f"Stale message check finished. Total claimed: {messages_claimed_total}, Processed: {processed_claim_count}, Acked: {acked_claim_count}, Errors: {error_claim_count}")

# --- New Redis Stream Consumer Task ---
async def consume_redis_stream():
    """Background task to consume transcription segments from Redis Stream."""
    # Use '>' to only read new messages, as pending/stale ones handled at startup
    last_processed_id = '>' 
    logger.info(f"Starting main consumer loop for '{CONSUMER_NAME}', reading new messages ('>')...")

    while True:
        try:
            response = await redis_client.xreadgroup(
                groupname=REDIS_CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={REDIS_STREAM_NAME: last_processed_id},
                count=REDIS_STREAM_READ_COUNT,
                block=REDIS_STREAM_BLOCK_MS 
            )

            if not response:
                # Timeout occurred, loop and wait again
                continue

            # Response format: [[stream_name, [[message_id, {field: value, ...}], ...]]]
            for stream, messages in response:
                message_ids_to_ack = []
                processed_count = 0
                logger.debug(f"Received {len(messages)} new messages from stream '{stream}'")

                for message_id, message_data in messages:
                    should_ack = False
                    processed_count += 1
                    try:
                        # Call the refactored processing helper
                        should_ack = await process_stream_message(message_id, message_data)
                    except Exception as e:
                        # Log any error during the helper call itself, although it should handle most internally
                        logger.error(f"Critical error during main loop processing helper call for {message_id}: {e}", exc_info=True)
                        should_ack = False # Do not ACK if the helper itself crashed unexpectedly
                    if should_ack:
                        message_ids_to_ack.append(message_id)
                        
                # Acknowledge messages processed in this batch
                if message_ids_to_ack:
                    try:
                        await redis_client.xack(REDIS_STREAM_NAME, REDIS_CONSUMER_GROUP, *message_ids_to_ack)
                        logger.debug(f"Acknowledged {len(message_ids_to_ack)}/{processed_count} messages from batch: {message_ids_to_ack}")
                    except Exception as e:
                        logger.error(f"Failed to acknowledge messages {message_ids_to_ack}: {e}", exc_info=True)
                        # Messages will remain pending and might be re-processed by this or another consumer
        
        except asyncio.CancelledError:
            logger.info("Redis Stream consumer task cancelled.")
            break
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis connection error in stream consumer: {e}. Retrying after delay...", exc_info=True)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unhandled error in Redis Stream consumer loop: {e}", exc_info=True)
            await asyncio.sleep(5)

# --- Background task for processing Redis segments into PostgreSQL ---
# (Keep existing process_redis_to_postgres function as is)
async def process_redis_to_postgres():
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
            # Sleep at the beginning of the loop
            await asyncio.sleep(BACKGROUND_TASK_INTERVAL)
            
            logger.debug("Background processor checking for immutable segments in Redis Hashes...")
            
            # Get list of active meetings
            meeting_ids_raw = await redis_client.smembers("active_meetings")
            if not meeting_ids_raw:
                logger.debug("No active meetings found in Redis Set")
                continue
                
            meeting_ids = [mid for mid in meeting_ids_raw]
            logger.debug(f"Found {len(meeting_ids)} active meetings in Redis Set")
            
            # Prepare batch storage
            batch_to_store = []
            segments_to_delete_from_redis = {}  # Dict of meeting_id -> set of start_times
            
            # Get database session for this batch using the session factory directly
            async with async_session_local() as db:
                # Process each meeting
                for meeting_id_str in meeting_ids:
                    try:
                        meeting_id = int(meeting_id_str)
                        hash_key = f"meeting:{meeting_id}:segments"
                        
                        # Get all segments for this meeting
                        redis_segments = await redis_client.hgetall(hash_key)
                        
                        if not redis_segments:
                            # If no segments, remove from active meetings set
                            await redis_client.srem("active_meetings", meeting_id_str)
                            logger.debug(f"Removed empty meeting {meeting_id} from active meetings set")
                            continue
                            
                        logger.debug(f"Processing {len(redis_segments)} segments from Redis Hash for meeting {meeting_id}")
                        
                        # Calculate the immutability threshold time
                        immutability_time = datetime.utcnow() - timedelta(seconds=IMMUTABILITY_THRESHOLD)
                        
                        # Process each segment from the Hash
                        for start_time_str, segment_json in redis_segments.items():
                            try:
                                segment_data = json.loads(segment_json)
                                segment_session_uid = segment_data.get("session_uid") # Extract session_uid
                                # Check for 'updated_at' key added by the stream consumer
                                if 'updated_at' not in segment_data:
                                     logger.warning(f"Segment {start_time_str} in meeting {meeting_id} hash is missing 'updated_at'. Skipping immutability check.")
                                     continue # Or handle differently based on policy
                                
                                segment_updated_at_str = segment_data['updated_at'].replace('Z', '+00:00') # Ensure timezone aware for fromisoformat
                                segment_updated_at = datetime.fromisoformat(segment_updated_at_str)
                                
                                # Check if segment is old enough to be considered immutable
                                # Ensure comparison is between timezone-aware datetimes or both naive UTC
                                if segment_updated_at.replace(tzinfo=None) < immutability_time: # Compare naive UTC
                                    # Apply filtering
                                    if transcription_filter.filter_segment(segment_data['text'], language=segment_data.get('language')):
                                        # Create Transcription object
                                        new_transcription = create_transcription_object(
                                            meeting_id=meeting_id,
                                            start=float(start_time_str),
                                            end=segment_data['end_time'],
                                            text=segment_data['text'],
                                            language=segment_data.get('language'),
                                            session_uid=segment_session_uid, # Pass session_uid
                                            speaker_id=segment_data.get('speaker_id'),  # Pass speaker_id 
                                            speaker_name=segment_data.get('speaker_name')  # Pass speaker_name
                                        )
                                        batch_to_store.append(new_transcription)
                                    
                                    # Mark for deletion from Redis Hash
                                    segments_to_delete_from_redis.setdefault(meeting_id, set()).add(start_time_str)
                            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                                logger.error(f"Error processing segment {start_time_str} from hash for meeting {meeting_id}: {e}")
                                # Still mark for deletion to avoid processing errors repeatedly
                                segments_to_delete_from_redis.setdefault(meeting_id, set()).add(start_time_str)
                    
                    except Exception as e:
                        logger.error(f"Error processing meeting {meeting_id_str} in Redis-to-PG task: {e}", exc_info=True)
                
                # Write batch to PostgreSQL if we have segments
                if batch_to_store:
                    try:
                        db.add_all(batch_to_store)
                        await db.commit()
                        logger.info(f"Stored {len(batch_to_store)} segments to PostgreSQL from {len(segments_to_delete_from_redis)} meetings")
                        
                        # Delete processed segments from Redis Hashes
                        for meeting_id, start_times in segments_to_delete_from_redis.items():
                            if start_times:
                                hash_key = f"meeting:{meeting_id}:segments"
                                await redis_client.hdel(hash_key, *start_times)
                                logger.debug(f"Deleted {len(start_times)} processed segments for meeting {meeting_id} from Redis Hash")
                    except Exception as e:
                        logger.error(f"Error committing batch to PostgreSQL: {e}", exc_info=True)
                        await db.rollback()
                else:
                    logger.debug("No segments ready for PostgreSQL storage this interval.")
        
        except asyncio.CancelledError:
            logger.info("Redis-to-PostgreSQL processor task cancelled")
            break
        # Use redis.exceptions here
        except redis.exceptions.ConnectionError as e:
             logger.error(f"Redis connection error in Redis-to-PG task: {e}. Retrying after delay...", exc_info=True)
             await asyncio.sleep(5) # Wait before retrying 
        except Exception as e:
            logger.error(f"Unhandled error in Redis-to-PostgreSQL processor: {e}", exc_info=True)
            # Don't break the loop - keep trying after sleep
            await asyncio.sleep(BACKGROUND_TASK_INTERVAL) # Ensure sleep even on error

# --- Helper Functions ---

# Simplified function - assumes meeting_id is valid
def create_transcription_object(meeting_id: int, start: float, end: float, text: str, language: Optional[str], session_uid: Optional[str], speaker_id: Optional[str] = None, speaker_name: Optional[str] = None) -> Transcription:
    """Creates a Transcription ORM object without adding/committing."""
    return Transcription(
        meeting_id=meeting_id,
        start_time=start,
        end_time=end,
        text=text,
        language=language,
        session_uid=session_uid, # Add session_uid
        speaker_id=speaker_id,  # Add speaker_id 
        speaker_name=speaker_name,  # Add speaker_name
        created_at=datetime.utcnow() # Record creation time in DB
    )

# --- API Endpoints (Health, Get Meetings, Get Transcripts) ---

@app.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint"""
    redis_status = "healthy"
    db_status = "healthy"
    
    try:
        if not redis_client: raise ValueError("Redis client not initialized")
        await redis_client.ping()
    except Exception as e:
        redis_status = f"unhealthy: {str(e)}"
    
    try:
        # Use the injected session 'db'
        await db.execute(text("SELECT 1")) 
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    return HealthResponse(
        status="healthy" if redis_status == "healthy" and db_status == "healthy" else "unhealthy",
        redis=redis_status,
        database=db_status,
        timestamp=datetime.now().isoformat()
    )

@app.get("/meetings", 
         response_model=MeetingListResponse,
         summary="Get list of all meetings for the current user",
         dependencies=[Depends(get_current_user)])
async def get_meetings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns a list of all meetings initiated by the authenticated user."""
    stmt = select(Meeting).where(Meeting.user_id == current_user.id).order_by(Meeting.created_at.desc())
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    return MeetingListResponse(meetings=[MeetingResponse.from_orm(m) for m in meetings])
    
@app.get("/transcripts/{platform}/{native_meeting_id}",
         response_model=TranscriptionResponse,
         summary="Get transcript for a specific meeting by platform and native ID",
         dependencies=[Depends(get_current_user)])
async def get_transcript_by_native_id(
    platform: Platform,
    native_meeting_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the meeting details and transcript segments for a meeting specified by its platform and native ID.
    Finds the *latest* matching meeting record for the user.
    
    Combines data from both PostgreSQL (immutable segments) and Redis Hashes (mutable segments), sorting chronologically based on session start times.
    """
    logger.debug(f"[API] User {current_user.id} requested transcript for {platform.value} / {native_meeting_id}")

    # 1. Find the latest meeting matching platform and native ID for the user
    stmt_meeting = select(Meeting).where(
                Meeting.user_id == current_user.id,
        Meeting.platform == platform.value,
        Meeting.platform_specific_id == native_meeting_id
    ).order_by(Meeting.created_at.desc())

    logger.debug(f"[API] Executing meeting lookup query...")
    result_meeting = await db.execute(stmt_meeting)
    meeting = result_meeting.scalars().first()
    
    if not meeting:
        logger.warning(f"[API] No meeting found for user {current_user.id}, platform '{platform.value}', native ID '{native_meeting_id}'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
        )

    internal_meeting_id = meeting.id
    logger.debug(f"[API] Found meeting record ID {internal_meeting_id}")

    # 2. Fetch session start times for this meeting
    logger.debug(f"[API Meet {internal_meeting_id}] Fetching session start times...")
    stmt_sessions = select(MeetingSession).where(MeetingSession.meeting_id == internal_meeting_id)
    result_sessions = await db.execute(stmt_sessions)
    sessions = result_sessions.scalars().all()
    session_times: Dict[str, datetime] = {session.session_uid: session.session_start_time for session in sessions}
    logger.debug(f"[API Meet {internal_meeting_id}] Found {len(session_times)} sessions: {list(session_times.keys())}")
    if not session_times:
        logger.warning(f"[API Meet {internal_meeting_id}] No session start times found in DB. Sorting may be inaccurate if reconnections occurred.")

    # 3. Fetch transcript segments from PostgreSQL (immutable segments)
    logger.debug(f"[API Meet {internal_meeting_id}] Fetching segments from PostgreSQL...")
    stmt_transcripts = select(Transcription).where(
        Transcription.meeting_id == internal_meeting_id
    )
    # Note: No longer sorting by start_time here, will sort by calculated absolute time later
    result_transcripts = await db.execute(stmt_transcripts)
    db_segments = result_transcripts.scalars().all()
    logger.debug(f"[API Meet {internal_meeting_id}] Retrieved {len(db_segments)} segments from PostgreSQL.")
    
    # 4. Fetch segments from Redis (mutable segments)
    hash_key = f"meeting:{internal_meeting_id}:segments"
    redis_segments_raw = {}
    logger.debug(f"[API Meet {internal_meeting_id}] Fetching segments from Redis Hash: {hash_key}...")
    try:
        if redis_client:
            redis_segments_raw = await redis_client.hgetall(hash_key)
            logger.debug(f"[API Meet {internal_meeting_id}] Retrieved {len(redis_segments_raw)} raw segments from Redis Hash.")
        else: 
            logger.error(f"[API Meet {internal_meeting_id}] Redis client not available for fetching mutable segments")
    except Exception as e:
        logger.error(f"[API Meet {internal_meeting_id}] Failed to fetch mutable segments from Redis: {e}", exc_info=True)
        # redis_segments_raw remains empty, allowing processing to continue with DB segments only
    
    # 5. Calculate absolute times and merge segments
    logger.debug(f"[API Meet {internal_meeting_id}] Calculating absolute times and merging...")
    # Store as: {relative_start_str: (absolute_datetime, segment_object)} to handle overwrites correctly
    merged_segments_with_abs_time: Dict[str, Tuple[datetime, TranscriptionSegment]] = {}

    # Process PostgreSQL segments first
    for segment in db_segments:
        key = f"{segment.start_time:.3f}"
        session_uid = segment.session_uid
        session_start = session_times.get(session_uid)
        if session_uid and session_start:
            try:
                # Ensure session_start is timezone-aware (should be from DB)
                if session_start.tzinfo is None:
                     session_start = session_start.replace(tzinfo=timezone.utc)
                     
                # Calculate absolute start and end times
                absolute_start_time = session_start + timedelta(seconds=segment.start_time)
                absolute_end_time = session_start + timedelta(seconds=segment.end_time)
                
                segment_obj = TranscriptionSegment(
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    text=segment.text,
                    language=segment.language,
                    created_at=segment.created_at, # Corrected previously
                    # ---> ADD Populate absolute times <----
                    absolute_start_time=absolute_start_time,
                    absolute_end_time=absolute_end_time
                    # ---> END ADD <----
                )
                merged_segments_with_abs_time[key] = (absolute_start_time, segment_obj) # Use abs start for sorting key
            except Exception as calc_err:
                 logger.error(f"[API Meet {internal_meeting_id}] Error calculating absolute time for DB segment {key} (UID: {session_uid}): {calc_err}")
        else:
            logger.warning(f"[API Meet {internal_meeting_id}] Missing session UID ({session_uid}) or start time for DB segment {key}. Cannot calculate absolute time.")
            # Fallback: Use meeting creation time as rough offset? Or skip?
            # Skipping for now to ensure accuracy, but could add fallback if needed.

    # Process Redis segments (overwriting DB ones with same relative start_time)
    for start_time_str, segment_json in redis_segments_raw.items():
        try:
            segment_data = json.loads(segment_json)
            session_uid_from_redis = segment_data.get("session_uid") # Get UID stored in Redis
            
            # ---> START FIX: Handle potentially prefixed UID from Redis <-----
            potential_session_key = session_uid_from_redis # Assume it's the correct key first
            if session_uid_from_redis:
                # Check for known prefixes and strip if found
                # This assumes prefixes end with '_' (e.g., 'google_meet_', 'zoom_')
                # Add other platform prefixes as needed
                prefixes_to_check = [f"{p.value}_" for p in Platform]
                for prefix in prefixes_to_check:
                    if session_uid_from_redis.startswith(prefix):
                        potential_session_key = session_uid_from_redis[len(prefix):]
                        logger.debug(f"[API Meet {internal_meeting_id}] Stripped prefix '{prefix}' from Redis UID '{session_uid_from_redis}', using key '{potential_session_key}' for lookup.")
                        break # Stop after first match
            # ---> END FIX <-----

            # Use the potentially corrected key for lookup
            session_start = session_times.get(potential_session_key) 

            # Original check using the potentially corrected key and original redis uid
            if 'end_time' in segment_data and 'text' in segment_data and session_uid_from_redis and session_start:
                try:
                    # Ensure session_start is timezone-aware
                    if session_start.tzinfo is None:
                         session_start = session_start.replace(tzinfo=timezone.utc)
                         
                    relative_start_time = float(start_time_str)
                    # Calculate absolute start and end times
                    absolute_start_time = session_start + timedelta(seconds=relative_start_time)
                    absolute_end_time = session_start + timedelta(seconds=segment_data['end_time'])

                    segment_obj = TranscriptionSegment(
                        start_time=relative_start_time,
                        end_time=segment_data['end_time'],
                        text=segment_data['text'],
                        language=segment_data.get('language'), # Corrected previously
                        # created_at will be None for Redis segments
                        # ---> ADD Populate absolute times <----
                        absolute_start_time=absolute_start_time,
                        absolute_end_time=absolute_end_time
                        # ---> END ADD <----
                    )
                    merged_segments_with_abs_time[start_time_str] = (absolute_start_time, segment_obj) # Overwrites if key exists, use abs start for sorting
                except Exception as calc_err:
                    logger.error(f"[API Meet {internal_meeting_id}] Error calculating absolute time for Redis segment {start_time_str} (UID: {session_uid_from_redis}): {calc_err}")
            else:
                # Log reason for skipping
                if not ('end_time' in segment_data and 'text' in segment_data):
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} due to missing keys (end_time/text). JSON: {segment_json[:100]}...")
                elif not session_uid_from_redis: # Check original UID from redis for logging
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} due to missing session_uid in Redis data. JSON: {segment_json[:100]}...")
                elif not session_start: # Check if lookup failed after potential stripping
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} with original UID {session_uid_from_redis} (lookup key: {potential_session_key}) because session start time not found in DB.")
                else: # Should not happen
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} for unknown reason.")

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.error(f"[API Meet {internal_meeting_id}] Error parsing Redis segment {start_time_str}: {e}")

    # 6. Sort based on calculated absolute time
    # Values are tuples: (absolute_datetime, TranscriptionSegment_object)
    sorted_segment_tuples = sorted(merged_segments_with_abs_time.values(), key=lambda item: item[0])

    # Extract final segment objects from sorted tuples
    sorted_segments = [segment_obj for abs_time, segment_obj in sorted_segment_tuples]
    logger.info(f"[API Meet {internal_meeting_id}] Merged and sorted into {len(sorted_segments)} total segments based on absolute time.")
    
    # 7. Construct the response using the found meeting and segments
    meeting_details = MeetingResponse.from_orm(meeting)

    # Combine into the final response model
    response_data = meeting_details.dict() # Get meeting data as dict
    response_data["segments"] = sorted_segments # Add correctly sorted segments list

    return TranscriptionResponse(**response_data)

# Helper for token validation reused by stream consumer
async def get_user_by_token(token: str, db: AsyncSession) -> Optional[User]:
    """Validates an API token and returns the associated User or raises HTTPException."""
    if not token:
        # Raise specific error if token is missing in stream data
        raise ValueError("Missing API token in stream data") 
    
    result = await db.execute(
        select(User).join(APIToken).where(APIToken.token == token)
    )
    user = result.scalars().first()
    
    if not user:
        logger.warning(f"Invalid API token provided in stream data: {token[:5]}...")
        # Raise specific error if token is invalid
        raise ValueError(f"Invalid API token") 
    return user

if __name__ == "__main__":
    # Removed uvicorn runner, rely on Docker CMD
    pass 