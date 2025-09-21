import logging
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy import select, and_, func, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from shared_models.database import get_db
from shared_models.models import User, Meeting, Transcription, MeetingSession
from shared_models.schemas import (
    HealthResponse,
    MeetingResponse,
    MeetingListResponse,
    TranscriptionResponse,
    Platform,
    TranscriptionSegment,
    MeetingUpdate,
    MeetingCreate,
    MeetingStatus
)

from config import IMMUTABILITY_THRESHOLD
from filters import TranscriptionFilter
from api.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()
class WsMeetingRef(MeetingCreate):
    """
    Schema for WS subscription meeting reference.
    Inherits validation from MeetingCreate but only platform and native_meeting_id are relevant.
    """
    class Config:
        extra = 'ignore'

class WsAuthorizeSubscribeRequest(BaseModel):
    meetings: List[WsMeetingRef]

class WsAuthorizeSubscribeResponse(BaseModel):
    authorized: List[Dict[str, str]]
    errors: List[str] = []


async def _get_full_transcript_segments(
    internal_meeting_id: int,
    db: AsyncSession,
    redis_c: aioredis.Redis
) -> List[TranscriptionSegment]:
    """
    Core logic to fetch and merge transcript segments from PG and Redis.
    """
    logger.debug(f"[_get_full_transcript_segments] Fetching for meeting ID {internal_meeting_id}")
    
    # 1. Fetch session start times for this meeting
    stmt_sessions = select(MeetingSession).where(MeetingSession.meeting_id == internal_meeting_id)
    result_sessions = await db.execute(stmt_sessions)
    sessions = result_sessions.scalars().all()
    session_times: Dict[str, datetime] = {session.session_uid: session.session_start_time for session in sessions}
    if not session_times:
        logger.warning(f"[_get_full_transcript_segments] No session start times found in DB for meeting {internal_meeting_id}.")

    # 2. Fetch transcript segments from PostgreSQL (immutable segments)
    stmt_transcripts = select(Transcription).where(Transcription.meeting_id == internal_meeting_id)
    result_transcripts = await db.execute(stmt_transcripts)
    db_segments = result_transcripts.scalars().all()

    # 3. Fetch segments from Redis (mutable segments)
    hash_key = f"meeting:{internal_meeting_id}:segments"
    redis_segments_raw = {}
    if redis_c:
        try:
            redis_segments_raw = await redis_c.hgetall(hash_key)
        except Exception as e:
            logger.error(f"[_get_full_transcript_segments] Failed to fetch from Redis hash {hash_key}: {e}", exc_info=True)

    # 4. Calculate absolute times and merge segments
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
                    speaker=segment.speaker,
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
                # This logic to strip prefixes is brittle. A better solution would be to store the canonical session_uid.
                # For now, keeping it to match previous behavior.
                prefixes_to_check = [f"{p.value}_" for p in Platform]
                for prefix in prefixes_to_check:
                    if session_uid_from_redis.startswith(prefix):
                        potential_session_key = session_uid_from_redis[len(prefix):]
                        break
            session_start = session_times.get(potential_session_key) 
            if 'end_time' in segment_data and 'text' in segment_data and session_uid_from_redis and session_start:
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
                    speaker=segment_data.get('speaker'),
                    absolute_start_time=absolute_start_time,
                    absolute_end_time=absolute_end_time
                )
                merged_segments_with_abs_time[start_time_str] = (absolute_start_time, segment_obj)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.error(f"[_get_full_transcript_segments] Error parsing Redis segment {start_time_str} for meeting {internal_meeting_id}: {e}")

    # 5. Sort based on calculated absolute time and return
    sorted_segment_tuples = sorted(merged_segments_with_abs_time.values(), key=lambda item: item[0])
    segments = [segment_obj for abs_time, segment_obj in sorted_segment_tuples]
    
    # 6. Deduplicate overlapping segments with identical text
    deduped: List[TranscriptionSegment] = []
    for seg in segments:
        if not deduped:
            deduped.append(seg)
            continue

        last = deduped[-1]
        same_text = (seg.text or "").strip() == (last.text or "").strip()
        overlaps = max(seg.start_time, last.start_time) < min(seg.end_time, last.end_time)

        if same_text and overlaps:
            # If current is fully inside last → drop current
            if seg.start_time >= last.start_time and seg.end_time <= last.end_time:
                continue
            # If current fully contains last → replace with current
            if seg.start_time <= last.start_time and seg.end_time >= last.end_time:
                deduped[-1] = seg
                continue

        deduped.append(seg)

    return deduped

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
    Combines data from both PostgreSQL (immutable segments) and Redis Hashes (mutable segments).
    """
    logger.debug(f"[API] User {current_user.id} requested transcript for {platform.value} / {native_meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None)
    
    #TODO: here we want to get a union of the meeting for platform/native meeting id instead of just the latest
    stmt_meeting = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform.value,
        Meeting.platform_specific_id == native_meeting_id
    ).order_by(Meeting.created_at.desc())

    result_meeting = await db.execute(stmt_meeting)
    meeting = result_meeting.scalars().first()
    
    if not meeting:
        logger.warning(f"[API] No meeting found for user {current_user.id}, platform '{platform.value}', native ID '{native_meeting_id}'")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
        )

    internal_meeting_id = meeting.id
    logger.debug(f"[API] Found meeting record ID {internal_meeting_id}, fetching segments...")

    sorted_segments = await _get_full_transcript_segments(internal_meeting_id, db, redis_c)
    
    logger.info(f"[API Meet {internal_meeting_id}] Merged and sorted into {len(sorted_segments)} total segments.")
    
    meeting_details = MeetingResponse.from_orm(meeting)
    response_data = meeting_details.dict()
    response_data["segments"] = sorted_segments
    return TranscriptionResponse(**response_data)


@router.post("/ws/authorize-subscribe",
            response_model=WsAuthorizeSubscribeResponse,
            summary="Authorize WS subscription for meetings",
            description="Validates that the authenticated user is allowed to subscribe to the given meetings and that identifiers are valid.",
            dependencies=[Depends(get_current_user)])
async def ws_authorize_subscribe(
    payload: WsAuthorizeSubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    authorized: List[Dict[str, str]] = []
    errors: List[str] = []

    meetings = payload.meetings or []
    if not meetings:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="'meetings' must be a non-empty list")

    for idx, meeting_ref in enumerate(meetings):
        platform_value = meeting_ref.platform.value if isinstance(meeting_ref.platform, Platform) else str(meeting_ref.platform)
        native_id = meeting_ref.native_meeting_id

        # Validate platform/native ID format via construct_meeting_url
        try:
            constructed = Platform.construct_meeting_url(platform_value, native_id)
        except Exception:
            constructed = None
        if not constructed:
            errors.append(f"meetings[{idx}] invalid native_meeting_id for platform '{platform_value}'")
            continue

        stmt_meeting = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform_value,
            Meeting.platform_specific_id == native_id
        ).order_by(Meeting.created_at.desc()).limit(1)

        result = await db.execute(stmt_meeting)
        meeting = result.scalars().first()
        if not meeting:
            errors.append(f"meetings[{idx}] not authorized or not found for user")
            continue

        authorized.append({"platform": platform_value, "native_id": native_id})

    return WsAuthorizeSubscribeResponse(authorized=authorized, errors=errors)


@router.get("/internal/transcripts/{meeting_id}",
            response_model=List[TranscriptionSegment],
            summary="[Internal] Get all transcript segments for a meeting",
            include_in_schema=False)
async def get_transcript_internal(
    meeting_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Internal endpoint for services to fetch all transcript segments for a given meeting ID."""
    logger.debug(f"[Internal API] Transcript segments requested for meeting {meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None)
    
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting with ID {meeting_id} not found."
        )
        
    segments = await _get_full_transcript_segments(meeting_id, db, redis_c)
    return segments

@router.patch("/meetings/{platform}/{native_meeting_id}",
             response_model=MeetingResponse,
             summary="Update meeting data by platform and native ID",
             dependencies=[Depends(get_current_user)])
async def update_meeting_data(
    platform: Platform,
    native_meeting_id: str,
    meeting_update: MeetingUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Updates the user-editable data (name, participants, languages, notes) for the latest meeting matching the platform and native ID."""
    
    logger.info(f"[API] User {current_user.id} updating meeting {platform.value}/{native_meeting_id}")
    logger.debug(f"[API] Raw meeting_update object: {meeting_update}")
    logger.debug(f"[API] meeting_update.data type: {type(meeting_update.data)}")
    logger.debug(f"[API] meeting_update.data content: {meeting_update.data}")
    
    stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform.value,
        Meeting.platform_specific_id == native_meeting_id
    ).order_by(Meeting.created_at.desc())
    
    result = await db.execute(stmt)
    meeting = result.scalars().first()
    
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
        )
        
    # Extract update data from the MeetingDataUpdate object
    try:
        if hasattr(meeting_update.data, 'dict'):
            # meeting_update.data is a MeetingDataUpdate pydantic object
            update_data = meeting_update.data.dict(exclude_unset=True)
            logger.debug(f"[API] Extracted update_data via .dict(): {update_data}")
        else:
            # Fallback: meeting_update.data is already a dict
            update_data = meeting_update.data
            logger.debug(f"[API] Using update_data as dict: {update_data}")
    except AttributeError:
        # Handle case where data might be parsed differently
        update_data = meeting_update.data
        logger.debug(f"[API] Fallback update_data: {update_data}")
    
    # Remove None values from update_data
    update_data = {k: v for k, v in update_data.items() if v is not None}
    logger.debug(f"[API] Final update_data after filtering None values: {update_data}")
    
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data provided for update."
        )
        
    if meeting.data is None:
        meeting.data = {}
        logger.debug(f"[API] Initialized empty meeting.data")
        
    logger.debug(f"[API] Current meeting.data before update: {meeting.data}")
        
    # Only allow updating restricted fields: name, participants, languages, notes
    allowed_fields = {'name', 'participants', 'languages', 'notes'}
    updated_fields = []
    
    # Create a new copy of the data dict to ensure SQLAlchemy detects the change
    new_data = dict(meeting.data) if meeting.data else {}
    
    for key, value in update_data.items():
        if key in allowed_fields and value is not None:
            new_data[key] = value
            updated_fields.append(f"{key}={value}")
            logger.debug(f"[API] Updated field {key} = {value}")
        else:
            logger.debug(f"[API] Skipped field {key} (not in allowed_fields or value is None)")
    
    # Assign the new dict to ensure SQLAlchemy detects the change
    meeting.data = new_data
    
    # Mark the field as modified to ensure SQLAlchemy detects the change
    from sqlalchemy.orm import attributes
    attributes.flag_modified(meeting, "data")
    
    logger.info(f"[API] Updated fields: {', '.join(updated_fields) if updated_fields else 'none'}")
    logger.debug(f"[API] Final meeting.data after update: {meeting.data}")

    await db.commit()
    await db.refresh(meeting)
    
    logger.debug(f"[API] Meeting.data after commit and refresh: {meeting.data}")
    
    return MeetingResponse.from_orm(meeting)

@router.delete("/meetings/{platform}/{native_meeting_id}",
              summary="Delete meeting transcripts and anonymize meeting data",
              dependencies=[Depends(get_current_user)])
async def delete_meeting(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Purges transcripts and anonymizes meeting data for finalized meetings.
    
    Only allows deletion for meetings in finalized states (completed, failed).
    Deletes all transcripts but preserves meeting and session records for telemetry.
    Scrubs PII from meeting record while keeping telemetry data.
    """
    
    stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform.value,
        Meeting.platform_specific_id == native_meeting_id
    ).order_by(Meeting.created_at.desc())
    
    result = await db.execute(stmt)
    meeting = result.scalars().first()
    
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
        )
    
    internal_meeting_id = meeting.id
    
    # Check if already redacted (idempotency)
    if meeting.data and meeting.data.get('redacted'):
        logger.info(f"[API] Meeting {internal_meeting_id} already redacted, returning success")
        return {"message": f"Meeting {platform.value}/{native_meeting_id} transcripts already deleted and data anonymized"}
    
    # Check if meeting is in finalized state
    finalized_states = {MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value}
    if meeting.status not in finalized_states:
        logger.warning(f"[API] User {current_user.id} attempted to delete non-finalized meeting {internal_meeting_id} (status: {meeting.status})")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Meeting not finalized; cannot delete transcripts. Current status: {meeting.status}"
        )
    
    logger.info(f"[API] User {current_user.id} purging transcripts and anonymizing meeting {internal_meeting_id}")
    
    # Delete transcripts from PostgreSQL
    stmt_transcripts = select(Transcription).where(Transcription.meeting_id == internal_meeting_id)
    result_transcripts = await db.execute(stmt_transcripts)
    transcripts = result_transcripts.scalars().all()
    
    for transcript in transcripts:
        await db.delete(transcript)
    
    # Delete transcript segments from Redis and remove from active meetings
    redis_c = getattr(request.app.state, 'redis_client', None)
    if redis_c:
        try:
            hash_key = f"meeting:{internal_meeting_id}:segments"
            # Use pipeline for atomic operations
            async with redis_c.pipeline(transaction=True) as pipe:
                pipe.delete(hash_key)
                pipe.srem("active_meetings", str(internal_meeting_id))
                results = await pipe.execute()
            logger.debug(f"[API] Deleted Redis hash {hash_key} and removed from active_meetings")
        except Exception as e:
            logger.error(f"[API] Failed to delete Redis data for meeting {internal_meeting_id}: {e}")
    
    # Scrub PII from meeting record while preserving telemetry
    original_data = meeting.data or {}
    
    # Keep only telemetry fields
    telemetry_fields = {'status_transition', 'completion_reason', 'error', 'diagnostics'}
    scrubbed_data = {k: v for k, v in original_data.items() if k in telemetry_fields}
    
    # Add redaction marker for idempotency
    scrubbed_data['redacted'] = True
    
    # Update meeting record with scrubbed data
    meeting.platform_specific_id = None  # Clear native meeting ID (this makes constructed_meeting_url return None)
    meeting.data = scrubbed_data
    
    # Note: We keep Meeting and MeetingSession records for telemetry
    await db.commit()
    
    logger.info(f"[API] Successfully purged transcripts and anonymized meeting {internal_meeting_id}")
    
    return {"message": f"Meeting {platform.value}/{native_meeting_id} transcripts deleted and data anonymized"} 