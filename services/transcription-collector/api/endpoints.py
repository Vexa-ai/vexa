import logging
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select, and_, func, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy.orm import joinedload # Not directly used in the provided snippets for these endpoints

from shared_models.database import get_db
from shared_models.models import User, Meeting, Transcription, MeetingSession
from shared_models.schemas import (
    HealthResponse,
    MeetingResponse,
    MeetingListResponse,
    TranscriptionResponse,
    Platform,
    TranscriptionSegment
)

from config import IMMUTABILITY_THRESHOLD
from filters import TranscriptionFilter
from api.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request, db: AsyncSession = Depends(get_db)):
    """Health check endpoint"""
    redis_status = "healthy"
    db_status = "healthy"
    
    try:
        redis_c = getattr(request.app.state, 'redis_client', None)
        if not redis_c: raise ValueError("Redis client not initialized in app.state")
        await redis_c.ping()
    except Exception as e:
        redis_status = f"unhealthy: {str(e)}"
    
    try:
        await db.execute(text("SELECT 1")) 
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
    
    return HealthResponse(
        status="healthy" if redis_status == "healthy" and db_status == "healthy" else "unhealthy",
        redis=redis_status,
        database=db_status,
        timestamp=datetime.now().isoformat()
    )

@router.get("/meetings", 
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
    
@router.get("/transcripts/{platform}/{native_meeting_id}",
            response_model=TranscriptionResponse,
            summary="Get transcript for a specific meeting by platform and native ID",
            dependencies=[Depends(get_current_user)])
async def get_transcript_by_native_id(
    platform: Platform,
    native_meeting_id: str,
    request: Request, # Added for redis_client access
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the meeting details and transcript segments for a meeting specified by its platform and native ID.
    Finds the *latest* matching meeting record for the user.
    Combines data from both PostgreSQL (immutable segments) and Redis Hashes (mutable segments), sorting chronologically based on session start times.
    """
    logger.debug(f"[API] User {current_user.id} requested transcript for {platform.value} / {native_meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None) # Get redis_client from app.state
    local_transcription_filter = TranscriptionFilter() # Instantiate filter

    # 1. Find the latest meeting matching platform and native ID for the user
    stmt_meeting = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform.value, # Use platform.value if 'platform' is an Enum
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
    result_transcripts = await db.execute(stmt_transcripts)
    db_segments = result_transcripts.scalars().all()
    logger.debug(f"[API Meet {internal_meeting_id}] Retrieved {len(db_segments)} segments from PostgreSQL.")
    
    # 4. Fetch segments from Redis (mutable segments)
    hash_key = f"meeting:{internal_meeting_id}:segments"
    redis_segments_raw = {}
    logger.debug(f"[API Meet {internal_meeting_id}] Fetching segments from Redis Hash: {hash_key}...")
    try:
        if redis_c:
            # Check if the meeting is recent enough to warrant checking Redis
            # This logic was originally controlled by IMMUTABILITY_THRESHOLD in process_redis_to_postgres
            # We need a similar check here or always fetch from Redis and let client handle it.
            # For now, copying the old behavior of fetching if redis_client is available.
            # A more refined approach might involve checking meeting.updated_at or similar.
            redis_segments_raw = await redis_c.hgetall(hash_key)
            logger.debug(f"[API Meet {internal_meeting_id}] Retrieved {len(redis_segments_raw)} raw segments from Redis Hash.")
        else: 
            logger.error(f"[API Meet {internal_meeting_id}] Redis client not available from app.state for fetching mutable segments")
    except Exception as e:
        logger.error(f"[API Meet {internal_meeting_id}] Failed to fetch mutable segments from Redis: {e}", exc_info=True)
    
    # 5. Calculate absolute times and merge segments
    logger.debug(f"[API Meet {internal_meeting_id}] Calculating absolute times and merging...")
    merged_segments_with_abs_time: Dict[str, Tuple[datetime, TranscriptionSegment]] = {}

    for segment in db_segments:
        key = f"{segment.start_time:.3f}"
        session_uid = segment.session_uid
        session_start = session_times.get(session_uid)
        if session_uid and session_start:
            try:
                if session_start.tzinfo is None:
                     session_start = session_start.replace(tzinfo=timezone.utc)
                absolute_start_time = session_start + timedelta(seconds=segment.start_time)
                absolute_end_time = session_start + timedelta(seconds=segment.end_time)
                segment_obj = TranscriptionSegment(
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    text=segment.text,
                    language=segment.language,
                    created_at=segment.created_at,
                    absolute_start_time=absolute_start_time,
                    absolute_end_time=absolute_end_time
                )
                merged_segments_with_abs_time[key] = (absolute_start_time, segment_obj)
            except Exception as calc_err:
                 logger.error(f"[API Meet {internal_meeting_id}] Error calculating absolute time for DB segment {key} (UID: {session_uid}): {calc_err}")
        else:
            logger.warning(f"[API Meet {internal_meeting_id}] Missing session UID ({session_uid}) or start time for DB segment {key}. Cannot calculate absolute time.")

    for start_time_str, segment_json in redis_segments_raw.items():
        try:
            segment_data = json.loads(segment_json)
            session_uid_from_redis = segment_data.get("session_uid")
            potential_session_key = session_uid_from_redis
            if session_uid_from_redis:
                prefixes_to_check = [f"{p.value}_" for p in Platform]
                for prefix in prefixes_to_check:
                    if session_uid_from_redis.startswith(prefix):
                        potential_session_key = session_uid_from_redis[len(prefix):]
                        logger.debug(f"[API Meet {internal_meeting_id}] Stripped prefix '{prefix}' from Redis UID '{session_uid_from_redis}', using key '{potential_session_key}' for lookup.")
                        break
            session_start = session_times.get(potential_session_key) 
            if 'end_time' in segment_data and 'text' in segment_data and session_uid_from_redis and session_start:
                try:
                    if session_start.tzinfo is None:
                         session_start = session_start.replace(tzinfo=timezone.utc)
                    relative_start_time = float(start_time_str)
                    absolute_start_time = session_start + timedelta(seconds=relative_start_time)
                    absolute_end_time = session_start + timedelta(seconds=segment_data['end_time'])
                    segment_obj = TranscriptionSegment(
                        start_time=relative_start_time,
                        end_time=segment_data['end_time'],
                        text=segment_data['text'],
                        language=segment_data.get('language'),
                        absolute_start_time=absolute_start_time,
                        absolute_end_time=absolute_end_time
                    )
                    # Apply filtering for Redis segments
                    # The original filter logic was complex and based on IMMUTABILITY_THRESHOLD.
                    # Here, we assume all fetched Redis segments are candidates.
                    # A proper filter might need to be applied if TranscriptionFilter has such logic.
                    # For now, let's include it and it can be refined.
                    # The filter in main.py was used for *live* segments, this is on-demand retrieval.
                    # The IMMUTABILITY_THRESHOLD logic in main was for *writing* to PG.
                    # Here, we're *reading*. The main filter was `transcription_filter.filter_segments`
                    # which took a list of segments.
                    # This part of the logic needs careful review against the original filter's purpose.
                    # The `TranscriptionFilter` in `filters.py` might be for *live stream processing*.
                    # The old code did not explicitly filter Redis results in `get_transcript_by_native_id`
                    # using `transcription_filter.filter_segments` but rather used `IMMUTABILITY_THRESHOLD`
                    # implicitly by what `process_redis_to_postgres` *hadn't yet processed*.
                    # For now, I will include all segments from Redis if they are valid.
                    merged_segments_with_abs_time[start_time_str] = (absolute_start_time, segment_obj)
                except Exception as calc_err:
                    logger.error(f"[API Meet {internal_meeting_id}] Error calculating absolute time for Redis segment {start_time_str} (UID: {session_uid_from_redis}): {calc_err}")
            else:
                if not ('end_time' in segment_data and 'text' in segment_data):
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} due to missing keys (end_time/text). JSON: {segment_json[:100]}...")
                elif not session_uid_from_redis:
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} due to missing session_uid in Redis data. JSON: {segment_json[:100]}...")
                elif not session_start:
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} with original UID {session_uid_from_redis} (lookup key: {potential_session_key}) because session start time not found in DB.")
                else:
                     logger.warning(f"[API Meet {internal_meeting_id}] Skipping Redis segment {start_time_str} for unknown reason.")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.error(f"[API Meet {internal_meeting_id}] Error parsing Redis segment {start_time_str}: {e}")

    # 6. Sort based on calculated absolute time
    sorted_segment_tuples = sorted(merged_segments_with_abs_time.values(), key=lambda item: item[0])
    sorted_segments = [segment_obj for abs_time, segment_obj in sorted_segment_tuples]
    logger.info(f"[API Meet {internal_meeting_id}] Merged and sorted into {len(sorted_segments)} total segments based on absolute time.")
    
    # 7. Construct the response
    meeting_details = MeetingResponse.from_orm(meeting)
    response_data = meeting_details.dict()
    response_data["segments"] = sorted_segments
    return TranscriptionResponse(**response_data) 