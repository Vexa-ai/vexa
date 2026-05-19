import logging
import os
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from ..database import get_db, async_session_local
from ..models import Meeting, Transcription, MeetingSession, Recording
from ..storage import create_storage_client
from ..schemas import (
    MeetingResponse,
    MeetingListResponse,
    TranscriptionResponse,
    Platform,
    TranscriptionSegment,
    MeetingUpdate,
    MeetingCreate,
    MeetingStatus,
)
from ..auth import UserProxy

from .config import IMMUTABILITY_THRESHOLD
from .filters import TranscriptionFilter
from .auth import get_current_user, require_internal_secret

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


class WsMeetingRef(BaseModel):
    """Schema for WS subscription meeting reference — only platform + native_meeting_id needed."""
    platform: str
    native_meeting_id: str

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
    Fetch and merge transcript segments from Postgres and Redis by segment_id.
    No heuristic dedup — segment_id is the identity.
    Redis segments (live) take precedence over Postgres (persisted).
    """
    # 1. Session start times (for absolute time computation on legacy PG rows)
    stmt_sessions = select(MeetingSession).where(MeetingSession.meeting_id == internal_meeting_id)
    result_sessions = await db.execute(stmt_sessions)
    sessions = result_sessions.scalars().all()
    session_times: Dict[str, datetime] = {s.session_uid: s.session_start_time for s in sessions}

    # 2. Postgres segments (immutable, persisted)
    stmt = select(Transcription).where(Transcription.meeting_id == internal_meeting_id)
    result = await db.execute(stmt)
    db_segments = result.scalars().all()

    # 3. Redis segments (mutable, live)
    hash_key = f"meeting:{internal_meeting_id}:segments"
    redis_raw = {}
    if redis_c:
        try:
            redis_raw = await redis_c.hgetall(hash_key)
        except Exception as e:
            logger.error(f"[Segments] Redis fetch failed for {hash_key}: {e}")

    # 4. Merge by segment_id — Redis wins on conflict
    merged: Dict[str, TranscriptionSegment] = {}

    for seg in db_segments:
        key = seg.segment_id or f"pg:{seg.speaker or ''}:{seg.start_time:.3f}"
        session_start = session_times.get(seg.session_uid)
        if session_start:
            if session_start.tzinfo is None:
                session_start = session_start.replace(tzinfo=timezone.utc)
            abs_start = session_start + timedelta(seconds=seg.start_time)
            abs_end = session_start + timedelta(seconds=seg.end_time)
        else:
            abs_start = abs_end = None

        try:
            merged[key] = TranscriptionSegment(
                start_time=seg.start_time, end_time=seg.end_time,
                text=seg.text, language=seg.language, speaker=seg.speaker,
                created_at=seg.created_at, completed=True,
                absolute_start_time=abs_start, absolute_end_time=abs_end,
                segment_id=seg.segment_id,
            )
        except Exception as e:
            logger.error(f"[Segments] PG segment error {key}: {e}")

    for seg_key, segment_json in redis_raw.items():
        try:
            d = json.loads(segment_json)
            if not d.get('text', '').strip():
                continue

            key = d.get('segment_id') or seg_key

            # Compute absolute times from segment data or session start
            abs_start = abs_end = None
            abs_from_data = d.get("absolute_start_time")
            if abs_from_data:
                try:
                    s = abs_from_data if not abs_from_data.endswith('Z') else abs_from_data[:-1] + '+00:00'
                    abs_start = datetime.fromisoformat(s)
                    if abs_start.tzinfo is None:
                        abs_start = abs_start.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            abs_end_data = d.get("absolute_end_time")
            if abs_end_data:
                try:
                    s = abs_end_data if not abs_end_data.endswith('Z') else abs_end_data[:-1] + '+00:00'
                    abs_end = datetime.fromisoformat(s)
                    if abs_end.tzinfo is None:
                        abs_end = abs_end.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            # Fallback: compute from session start
            if not abs_start:
                uid = d.get("session_uid")
                if uid:
                    # Strip platform prefix if present
                    clean_uid = uid
                    for p in Platform:
                        pref = f"{p.value}_"
                        if uid.startswith(pref):
                            clean_uid = uid[len(pref):]
                            break
                    ss = session_times.get(clean_uid)
                    if ss:
                        if ss.tzinfo is None:
                            ss = ss.replace(tzinfo=timezone.utc)
                        abs_start = ss + timedelta(seconds=float(d.get("start_time", 0)))
                        abs_end = ss + timedelta(seconds=float(d.get("end_time", 0)))

            merged[key] = TranscriptionSegment(
                start_time=float(d.get("start_time", 0)),
                end_time=float(d.get("end_time", 0)),
                text=d['text'], language=d.get('language'),
                speaker=d.get('speaker'),
                completed=bool(d.get("completed", False)),
                absolute_start_time=abs_start, absolute_end_time=abs_end,
                segment_id=d.get('segment_id'),
            )
        except Exception as e:
            logger.error(f"[Segments] Redis segment error {seg_key}: {e}")

    # 5. Sort by absolute_start_time (or start_time as fallback)
    def sort_key(seg: TranscriptionSegment):
        if seg.absolute_start_time:
            return seg.absolute_start_time
        return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(merged.values(), key=sort_key)

@router.get("/meetings",
            response_model=MeetingListResponse,
            summary="Get list of all meetings for the current user",
            dependencies=[Depends(get_current_user)])
async def get_meetings(
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Optional[int] = Query(None, ge=1, le=100, description="Max meetings to return"),
    offset: Optional[int] = Query(None, ge=0, description="Number of meetings to skip"),
    status: Optional[str] = Query(None, description="Filter by status (active, completed, failed)"),
    platform: Optional[str] = Query(None, description="Filter by platform (google_meet, teams, zoom)"),
):
    """Returns a list of meetings initiated by the authenticated user."""
    stmt = select(Meeting).where(Meeting.user_id == current_user.id)
    if status:
        stmt = stmt.where(Meeting.status == status)
    if platform:
        stmt = stmt.where(Meeting.platform == platform)
    stmt = stmt.order_by(Meeting.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    return MeetingListResponse(meetings=[MeetingResponse.model_validate(m) for m in meetings])

@router.get("/transcripts/{platform}/{native_meeting_id}",
            response_model=TranscriptionResponse,
            response_model_exclude_none=False,
            summary="Get transcript for a specific meeting by platform and native ID",
            dependencies=[Depends(get_current_user)])
async def get_transcript_by_native_id(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    meeting_id: Optional[int] = Query(None, description="Optional specific database meeting ID."),
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the meeting details and transcript segments for a meeting specified by its platform and native ID."""
    logger.debug(f"[API] User {current_user.id} requested transcript for {platform.value} / {native_meeting_id}, meeting_id={meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None)

    if meeting_id is not None:
        stmt_meeting = select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        )
    else:
        stmt_meeting = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        ).order_by(Meeting.created_at.desc())

    result_meeting = await db.execute(stmt_meeting)
    meeting = result_meeting.scalars().first()

    if not meeting:
        if meeting_id is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for platform {platform.value}, ID {native_meeting_id}, and meeting_id {meeting_id}"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
            )

    internal_meeting_id = meeting.id
    sorted_segments = await _get_full_transcript_segments(internal_meeting_id, db, redis_c)

    logger.info(f"[API Meet {internal_meeting_id}] Merged and sorted into {len(sorted_segments)} total segments.")

    meeting_details = MeetingResponse.model_validate(meeting)
    response_data = meeting_details.model_dump()
    response_data["recordings"] = (meeting.data or {}).get("recordings", []) if isinstance(meeting.data, dict) else []
    response_data["notes"] = (meeting.data or {}).get("notes") if isinstance(meeting.data, dict) else None
    response_data["data"] = dict(meeting.data) if isinstance(meeting.data, dict) else {}
    response_data["speaker_events"] = (meeting.data or {}).get("speaker_events", []) if isinstance(meeting.data, dict) else []
    response_data["segments"] = sorted_segments
    return TranscriptionResponse(**response_data)


@router.post("/ws/authorize-subscribe",
            response_model=WsAuthorizeSubscribeResponse,
            summary="Authorize WS subscription for meetings",
            dependencies=[Depends(get_current_user)])
async def ws_authorize_subscribe(
    payload: WsAuthorizeSubscribeRequest,
    current_user: UserProxy = Depends(get_current_user),
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
            response_model_exclude_none=False,
            summary="[Internal] Get all transcript segments for a meeting",
            include_in_schema=False,
            dependencies=[Depends(require_internal_secret)])
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
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Updates the user-editable data (name, participants, languages, notes) for the latest meeting."""

    logger.info(f"[API] User {current_user.id} updating meeting {platform.value}/{native_meeting_id}")

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
            update_data = meeting_update.data.model_dump(exclude_unset=True)
        else:
            update_data = meeting_update.data
    except AttributeError:
        update_data = meeting_update.data

    # Remove None values from update_data
    update_data = {k: v for k, v in update_data.items() if v is not None}

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data provided for update."
        )

    if meeting.data is None:
        meeting.data = {}

    # Only allow updating restricted fields: name, participants, languages, notes
    allowed_fields = {'name', 'participants', 'languages', 'notes'}
    updated_fields = []

    # Create a new copy of the data dict to ensure SQLAlchemy detects the change
    new_data = dict(meeting.data) if meeting.data else {}

    for key, value in update_data.items():
        if key in allowed_fields and value is not None:
            new_data[key] = value
            updated_fields.append(f"{key}={value}")

    # Assign the new dict to ensure SQLAlchemy detects the change
    meeting.data = new_data

    # Mark the field as modified to ensure SQLAlchemy detects the change
    from sqlalchemy.orm import attributes
    attributes.flag_modified(meeting, "data")

    logger.info(f"[API] Updated fields: {', '.join(updated_fields) if updated_fields else 'none'}")

    await db.commit()
    await db.refresh(meeting)

    return MeetingResponse.model_validate(meeting)

@router.delete("/meetings/{platform}/{native_meeting_id}",
              summary="Delete meeting transcripts and anonymize meeting data",
              dependencies=[Depends(get_current_user)])
async def delete_meeting(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Purges transcripts and anonymizes meeting data for finalized meetings.
    Only allows deletion for meetings in finalized states (completed, failed).
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
    telemetry_fields = {'status_transition', 'completion_reason', 'error', 'diagnostics'}
    scrubbed_data = {k: v for k, v in original_data.items() if k in telemetry_fields}

    # Add redaction marker for idempotency
    scrubbed_data['redacted'] = True

    # Update meeting record with scrubbed data
    meeting.platform_specific_id = None
    meeting.data = scrubbed_data

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
