import logging
import os
import json
import string
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from shared_models.database import get_db, async_session_local
from shared_models.models import User, Meeting, Transcription, MeetingSession, Recording
from shared_models.storage import create_storage_client
from shared_models.schemas import (
    HealthResponse,
    MeetingResponse,
    MeetingListResponse,
    TranscriptionResponse,
    Platform,
    TranscriptionSegment,
    MeetingUpdate,
    MeetingCreate,
    MeetingStatus,
)

from config import IMMUTABILITY_THRESHOLD
from filters import TranscriptionFilter
from api.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_storage_targets_from_meeting_data(data: Optional[Dict]) -> List[Tuple[str, str]]:
    """Collect (storage_backend, storage_path) from meeting.data['recordings'] payload."""
    targets: List[Tuple[str, str]] = []
    if not isinstance(data, dict):
        return targets

    for rec in (data.get("recordings") or []):
        if not isinstance(rec, dict):
            continue
        for mf in (rec.get("media_files") or []):
            if not isinstance(mf, dict):
                continue
            path = mf.get("storage_path")
            if not isinstance(path, str) or not path:
                continue
            backend = mf.get("storage_backend")
            if not isinstance(backend, str) or not backend:
                backend = os.getenv("STORAGE_BACKEND", "minio")
            targets.append((str(backend).strip().lower(), path))

    return targets


async def _purge_recordings_for_meeting(
    db: AsyncSession,
    meeting: Meeting,
    user_id: int,
) -> Dict[str, int]:
    """
    Delete recording DB rows and storage objects for a meeting.
    Handles both meeting.data metadata mode and normalized Recording model mode.
    """
    # backend -> set(paths)
    targets_by_backend: Dict[str, set[str]] = {}
    for backend, path in _extract_storage_targets_from_meeting_data(meeting.data):
        targets_by_backend.setdefault(backend, set()).add(path)

    # Collect normalized recording rows/media paths and mark rows for deletion.
    # Some deployments still run in meeting_data-only mode without normalized recording tables.
    table_exists_result = await db.execute(text("SELECT to_regclass('public.recordings') IS NOT NULL"))
    recordings_table_exists = bool(table_exists_result.scalar())
    if recordings_table_exists:
        stmt_recordings = select(Recording).where(
            Recording.meeting_id == meeting.id,
            Recording.user_id == user_id,
        )
        result_recordings = await db.execute(stmt_recordings)
        recordings = result_recordings.scalars().all()
    else:
        logger.info("[API] recordings table unavailable in this environment; skipping model recording cleanup")
        recordings = []
    model_recordings_deleted = 0

    for recording in recordings:
        await db.refresh(recording, ["media_files"])
        for media_file in (recording.media_files or []):
            if media_file.storage_path:
                backend = (media_file.storage_backend or os.getenv("STORAGE_BACKEND", "minio")).strip().lower()
                targets_by_backend.setdefault(backend, set()).add(media_file.storage_path)
        await db.delete(recording)
        model_recordings_deleted += 1

    storage_files_deleted = 0
    storage_files_targeted = sum(len(v) for v in targets_by_backend.values())
    if storage_files_targeted:
        # Delete storage files best-effort (by per-file backend).
        clients: Dict[str, object] = {}

        for backend in list(targets_by_backend.keys()):
            if backend not in ("minio", "s3", "local"):
                logger.warning(f"[API] Unknown storage backend '{backend}', defaulting to 'minio'")
                targets_by_backend.setdefault("minio", set()).update(targets_by_backend.pop(backend))

        for backend in targets_by_backend.keys():
            try:
                clients[backend] = create_storage_client(backend)
            except Exception as e:
                logger.warning(f"[API] Failed to initialize storage client for backend '{backend}': {e}")

        for backend, paths in targets_by_backend.items():
            client = clients.get(backend)
            if client is None:
                continue
            for path in paths:
                try:
                    client.delete_file(path)
                    storage_files_deleted += 1
                except Exception as e:
                    logger.warning(f"[API] Failed deleting recording media from storage ({backend}:{path}): {e}")

    return {
        "model_recordings_deleted": model_recordings_deleted,
        "storage_files_deleted": storage_files_deleted,
        "storage_files_targeted": storage_files_targeted,
    }


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
    user_id: Optional[int] = None  # Include user_id for channel isolation


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
                    completed=True,
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
                # Parse created_at from updated_at if available, otherwise use None
                # Pydantic v2 requires Optional fields to be explicitly set, even if None
                created_at_value = None
                if 'updated_at' in segment_data and segment_data.get('updated_at'):
                    try:
                        updated_at_str = segment_data['updated_at']
                        if updated_at_str.endswith('Z'):
                            updated_at_str = updated_at_str[:-1] + '+00:00'
                        created_at_value = datetime.fromisoformat(updated_at_str)
                    except (ValueError, TypeError) as parse_err:
                        logger.debug(f"Failed to parse updated_at '{segment_data.get('updated_at')}' as datetime: {parse_err}")
                        created_at_value = None
                
                # Create segment object using model_validate with dict to ensure defaults are applied
                try:
                    segment_dict = {
                        'start_time': relative_start_time,
                        'end_time': segment_data['end_time'],
                        'text': segment_data['text'],
                        'language': segment_data.get('language'),
                        'speaker': segment_data.get('speaker'),
                        'completed': bool(segment_data.get("completed", False)),
                        'absolute_start_time': absolute_start_time,
                        'absolute_end_time': absolute_end_time
                    }
                    # Only add created_at if we have a value, let the default handle None
                    if created_at_value is not None:
                        segment_dict['created_at'] = created_at_value
                    
                    segment_obj = TranscriptionSegment.model_validate(segment_dict)
                except Exception as validation_err:
                    logger.error(f"[_get_full_transcript_segments] Validation error creating segment {start_time_str} for meeting {internal_meeting_id}: {validation_err}", exc_info=True)
                    # Skip this segment if validation fails
                    continue
                # Merge logic: Always include partial segments from Redis (they're the current active state)
                # If a completed segment exists in PostgreSQL with the same key, Redis partial takes precedence
                # because it represents the current state of an active transcription
                existing_segment_tuple = merged_segments_with_abs_time.get(start_time_str)
                if existing_segment_tuple:
                    existing_segment = existing_segment_tuple[1]
                    # If existing is completed and new is partial, prefer the partial (it's the current active state)
                    # This ensures GET endpoint always shows the latest partial segments
                    if existing_segment.completed and not segment_obj.completed:
                        # Replace with partial - it's the current state
                        pass
                    # If both are same completion status, Redis is more recent, so use it
                # Always add Redis segments (they're the current state)
                merged_segments_with_abs_time[start_time_str] = (absolute_start_time, segment_obj)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.error(f"[_get_full_transcript_segments] Error parsing Redis segment {start_time_str} for meeting {internal_meeting_id}: {e}", exc_info=True)
        except Exception as e:
            # Catch Pydantic ValidationError and other exceptions
            logger.error(f"[_get_full_transcript_segments] Unexpected error parsing Redis segment {start_time_str} for meeting {internal_meeting_id}: {e}", exc_info=True)

    # 5. Sort based on calculated absolute time and return
    sorted_segment_tuples = sorted(merged_segments_with_abs_time.values(), key=lambda item: item[0])
    segments = [segment_obj for abs_time, segment_obj in sorted_segment_tuples]
    
    # 6. Deduplicate overlapping segments (both identical and different text)
    deduped: List[TranscriptionSegment] = []
    for seg in segments:
        if not deduped:
            deduped.append(seg)
            continue

        last = deduped[-1]
        same_text = (seg.text or "").strip() == (last.text or "").strip()
        overlaps = max(seg.start_time, last.start_time) < min(seg.end_time, last.end_time)

        if overlaps:
            # Check if one segment is fully contained within another
            seg_fully_inside_last = seg.start_time >= last.start_time and seg.end_time <= last.end_time
            last_fully_inside_seg = last.start_time >= seg.start_time and last.end_time <= seg.end_time
            
            if same_text:
                # Same text: prefer the outer/longer segment
                if seg_fully_inside_last:
                    # Current is fully inside last → drop current
                    logger.debug(f"[Dedup Meet {internal_meeting_id}] Dropping segment '{seg.text}' ({seg.start_time}-{seg.end_time}) - fully contained in '{last.text}' ({last.start_time}-{last.end_time})")
                    continue
                if last_fully_inside_seg:
                    # Last is fully inside current → replace with current
                    logger.debug(f"[Dedup Meet {internal_meeting_id}] Replacing segment '{last.text}' ({last.start_time}-{last.end_time}) with '{seg.text}' ({seg.start_time}-{seg.end_time})")
                    deduped[-1] = seg
                    continue
            else:
                # Different text: prefer the longer/outer segment
                if seg_fully_inside_last:
                    # Current is fully inside last → drop current (prefer outer segment)
                    logger.debug(f"[Dedup Meet {internal_meeting_id}] Dropping segment '{seg.text}' ({seg.start_time}-{seg.end_time}) - fully contained in '{last.text}' ({last.start_time}-{last.end_time})")
                    continue
                if last_fully_inside_seg:
                    # Last is fully inside current → replace with current (prefer outer segment)
                    logger.debug(f"[Dedup Meet {internal_meeting_id}] Replacing segment '{last.text}' ({last.start_time}-{last.end_time}) with '{seg.text}' ({seg.start_time}-{seg.end_time})")
                    deduped[-1] = seg
                    continue
                
                # Partial-overlap heuristics (no full containment):
                # - "expansion": current is a longer revision that contains the previous text -> replace previous with current
                # - "tail-repeat": current is a tiny suffix/echo already present in previous -> drop current
                if not seg_fully_inside_last and not last_fully_inside_seg:
                    seg_text_norm = (seg.text or "").strip().lower()
                    last_text_norm = (last.text or "").strip().lower()
                    seg_text_clean = seg_text_norm.rstrip(string.punctuation).strip()
                    last_text_clean = last_text_norm.rstrip(string.punctuation).strip()

                    seg_duration = seg.end_time - seg.start_time
                    last_duration = last.end_time - last.start_time
                    overlap_start = max(seg.start_time, last.start_time)
                    overlap_end = min(seg.end_time, last.end_time)
                    overlap_duration = overlap_end - overlap_start
                    overlap_ratio_seg = overlap_duration / seg_duration if seg_duration > 0 else 0
                    overlap_ratio_last = overlap_duration / last_duration if last_duration > 0 else 0

                    # Expansion: last text appears inside seg text, and seg is "more complete" -> replace last with seg.
                    # This fixes cases like:
                    #   last="It was a milestone." (partial) then seg="It was a milestone to get ..." (completed)
                    seg_expands_last = (
                        bool(last_text_clean)
                        and bool(seg_text_clean)
                        and last_text_clean in seg_text_clean
                        and len(seg_text_clean) > len(last_text_clean)
                    )
                    last_completed = bool(getattr(last, "completed", False))
                    seg_completed = bool(getattr(seg, "completed", False))
                    if seg_expands_last and overlap_ratio_last >= 0.5 and (seg_completed or not last_completed):
                        logger.debug(
                            f"[Dedup Meet {internal_meeting_id}] Replacing shorter/partial segment '{last.text}' "
                            f"({last.start_time}-{last.end_time}, overlap={overlap_ratio_last:.1%}) with expansion '{seg.text}' "
                            f"({seg.start_time}-{seg.end_time})"
                        )
                        deduped[-1] = seg
                        continue

                    # Tail-repeat: seg text already appears in last text, and seg is tiny -> drop seg.
                    seg_is_tail_repeat = bool(seg_text_clean) and (
                        seg_text_clean in last_text_clean or seg_text_clean in last_text_norm
                    )
                    if seg_is_tail_repeat:
                        seg_word_count = len(seg_text_clean.split())
                        # Drop if: tiny segment (<=2 words, <1.5s) and overlaps at least a bit (>=25% of seg)
                        if seg_duration < 1.5 and seg_word_count <= 2 and overlap_ratio_seg >= 0.25:
                            logger.debug(
                                f"[Dedup Meet {internal_meeting_id}] Dropping tail-repeat fragment '{seg.text}' "
                                f"({seg.start_time}-{seg.end_time}, {seg_duration:.2f}s, {seg_word_count} words, overlap={overlap_ratio_seg:.1%}) "
                                f"- already present in '{last.text}' ({last.start_time}-{last.end_time})"
                            )
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
    return MeetingListResponse(meetings=[MeetingResponse.model_validate(m) for m in meetings])
    
@router.get("/transcripts/{platform}/{native_meeting_id}",
            response_model=TranscriptionResponse,
            summary="Get transcript for a specific meeting by platform and native ID",
            dependencies=[Depends(get_current_user)])
async def get_transcript_by_native_id(
    platform: Platform,
    native_meeting_id: str,
    request: Request, # Added for redis_client access
    meeting_id: Optional[int] = Query(None, description="Optional specific database meeting ID. If provided, returns that exact meeting. If not provided, returns the latest meeting for the platform/native_meeting_id combination."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the meeting details and transcript segments for a meeting specified by its platform and native ID.
    
    Behavior:
    - If meeting_id is provided: Returns the exact meeting with that database ID (must belong to user and match platform/native_meeting_id)
    - If meeting_id is not provided: Returns the latest matching meeting record for the user (backward compatible behavior)
    
    Combines data from both PostgreSQL (immutable segments) and Redis Hashes (mutable segments).
    """
    logger.debug(f"[API] User {current_user.id} requested transcript for {platform.value} / {native_meeting_id}, meeting_id={meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None)
    
    if meeting_id is not None:
        # Get specific meeting by database ID
        # Validate it belongs to user and matches platform/native_meeting_id for consistency
        stmt_meeting = select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        )
        logger.debug(f"[API] Looking for specific meeting ID {meeting_id} with platform/native validation")
    else:
        # Get latest meeting by platform/native_meeting_id (default behavior)
        stmt_meeting = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        ).order_by(Meeting.created_at.desc())
        logger.debug(f"[API] Looking for latest meeting for platform/native_id")

    result_meeting = await db.execute(stmt_meeting)
    meeting = result_meeting.scalars().first()
    
    if not meeting:
        if meeting_id is not None:
            logger.warning(f"[API] No meeting found for user {current_user.id}, platform '{platform.value}', native ID '{native_meeting_id}', meeting_id '{meeting_id}'")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for platform {platform.value}, ID {native_meeting_id}, and meeting_id {meeting_id}"
            )
        else:
            logger.warning(f"[API] No meeting found for user {current_user.id}, platform '{platform.value}', native ID '{native_meeting_id}'")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
            )

    internal_meeting_id = meeting.id
    logger.debug(f"[API] Found meeting record ID {internal_meeting_id}, fetching segments...")

    sorted_segments = await _get_full_transcript_segments(internal_meeting_id, db, redis_c)
    
    logger.info(f"[API Meet {internal_meeting_id}] Merged and sorted into {len(sorted_segments)} total segments.")
    
    meeting_details = MeetingResponse.model_validate(meeting)
    response_data = meeting_details.model_dump()
    # Surface recordings directly in transcript response to avoid an extra dashboard API call.
    response_data["recordings"] = (meeting.data or {}).get("recordings", []) if isinstance(meeting.data, dict) else []
    # Surface user-provided meeting notes (stored in meeting.data).
    response_data["notes"] = (meeting.data or {}).get("notes") if isinstance(meeting.data, dict) else None
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

        authorized.append({
            "platform": platform_value, 
            "native_id": native_id,
            "user_id": str(current_user.id),
            "meeting_id": str(meeting.id)
        })

    return WsAuthorizeSubscribeResponse(authorized=authorized, errors=errors, user_id=current_user.id)


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
            update_data = meeting_update.data.model_dump(exclude_unset=True)
            logger.debug(f"[API] Extracted update_data via .model_dump(): {update_data}")
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
    
    return MeetingResponse.model_validate(meeting)

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
    original_data = dict(meeting.data or {})
    
    # Check if already redacted (idempotency)
    if meeting.data and meeting.data.get('redacted'):
        logger.info(f"[API] Meeting {internal_meeting_id} already redacted, returning success")
        return {"message": f"Meeting {platform.value}/{native_meeting_id} artifacts already deleted and data anonymized"}
    
    # Check if meeting is in finalized state
    finalized_states = {MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value}
    if meeting.status not in finalized_states:
        logger.warning(f"[API] User {current_user.id} attempted to delete non-finalized meeting {internal_meeting_id} (status: {meeting.status})")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Meeting not finalized; cannot delete transcripts. Current status: {meeting.status}"
        )
    
    logger.info(f"[API] User {current_user.id} purging transcripts/recordings and anonymizing meeting {internal_meeting_id}")
    
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

    # Delete recordings artifacts (DB rows + storage files)
    recording_cleanup = await _purge_recordings_for_meeting(db, meeting, current_user.id)
    
    # Scrub PII from meeting record while preserving telemetry
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
    
    logger.info(
        f"[API] Successfully purged meeting {internal_meeting_id}: "
        f"{len(transcripts)} transcripts, "
        f"{recording_cleanup['model_recordings_deleted']} recording rows, "
        f"{recording_cleanup['storage_files_deleted']}/{recording_cleanup['storage_files_targeted']} recording files; "
        f"meeting anonymized"
    )

    return {
        "message": (
            f"Meeting {platform.value}/{native_meeting_id} transcripts and recording artifacts deleted; "
            "meeting data anonymized"
        )
    }
