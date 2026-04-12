"""/recordings/* and /internal/recordings/upload endpoints.

Recording management — /recordings/* and /internal/recordings/upload endpoints.
"""

import asyncio
import json
import logging
import os
import struct
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import and_, desc, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .database import get_db
from .models import Meeting, MeetingSession, Recording, MediaFile
from .schemas import (
    RecordingResponse,
    RecordingListResponse,
    RecordingStatus,
    RecordingSource,
)
from .storage import create_storage_client

from .auth import get_user_and_token
from .config import get_recording_metadata_mode
from .webhooks import send_event_webhook

logger = logging.getLogger("meeting_api.recordings")

router = APIRouter()

# --- Storage client (lazy init) ---
_storage_client = None


def get_storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = create_storage_client()
    return _storage_client


def _new_recording_numeric_id() -> int:
    return int(uuid_lib.uuid4().int % 900000000000 + 100000000000)


def _to_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_meeting_recording(recording: Dict[str, Any], meeting_id: int) -> Dict[str, Any]:
    rec = dict(recording or {})
    rec["meeting_id"] = rec.get("meeting_id") or meeting_id
    rec["source"] = rec.get("source") or RecordingSource.BOT.value
    rec["status"] = rec.get("status") or RecordingStatus.COMPLETED.value
    rec["media_files"] = rec.get("media_files") or []
    return rec


async def _list_meeting_data_recordings(db: AsyncSession, user_id: int, meeting_id: Optional[int] = None) -> List[Dict]:
    stmt = select(Meeting).where(Meeting.user_id == user_id)
    if meeting_id is not None:
        stmt = stmt.where(Meeting.id == meeting_id)
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    recordings: List[Dict] = []
    for m in meetings:
        if not isinstance(m.data, dict):
            continue
        for rec in m.data.get("recordings") or []:
            if isinstance(rec, dict):
                recordings.append(_normalize_meeting_recording(rec, m.id))
    recordings.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return recordings


async def _find_meeting_data_recording(db: AsyncSession, user_id: int, recording_id: int):
    # Use JSONB containment to find only meetings whose data->'recordings' array
    # contains an object with the target id, instead of scanning all user meetings.
    stmt = (
        select(Meeting)
        .where(
            Meeting.user_id == user_id,
            Meeting.data.isnot(None),
            Meeting.data["recordings"].cast(JSONB).isnot(None),
        )
        .where(
            text("data->'recordings' @> cast(:pattern as jsonb)").bindparams(
                pattern=json.dumps([{"id": recording_id}])
            )
        )
    )
    result = await db.execute(stmt)
    for m in result.scalars().all():
        if not isinstance(m.data, dict):
            continue
        for rec in m.data.get("recordings") or []:
            if isinstance(rec, dict) and int(rec.get("id", -1)) == recording_id:
                return m, _normalize_meeting_recording(rec, m.id)
    return None, None


# ---------------------------------------------------------------------------
# Segment concatenation helpers
# ---------------------------------------------------------------------------

WAV_HEADER_SIZE = 44


def _segment_storage_path(user_id: int, storage_id: int, session_uid: str, segment_index: int, media_format: str) -> str:
    """Build the storage key for an individual segment file."""
    return f"recordings/{user_id}/{storage_id}/segments/{session_uid}_seg_{segment_index:04d}.{media_format}"


def _concatenate_wav_segments(
    storage,
    user_id: int,
    storage_id: int,
    session_uid: str,
    media_format: str,
    final_segment_data: Optional[bytes] = None,
) -> Tuple[bytes, float]:
    """Download all segment files, concatenate WAV data, return (bytes, duration_seconds).

    The first segment's 44-byte header is kept intact.  Subsequent segments
    have their headers stripped.  The RIFF file-size (bytes 4-7) and data
    chunk size (bytes 40-43) are rewritten to match the actual payload.
    """
    prefix = f"recordings/{user_id}/{storage_id}/segments/{session_uid}_seg_"
    keys = storage.list_files(prefix)
    if not keys:
        raise ValueError(f"No segment files found for prefix {prefix}")

    # Download all segments in order (lexicographic = correct because of zero-padding)
    segment_blobs: List[bytes] = []
    for key in keys:
        segment_blobs.append(storage.download_file(key))

    # Append final_segment_data if provided (the segment that arrived with is_final)
    if final_segment_data:
        segment_blobs.append(final_segment_data)

    if not segment_blobs:
        raise ValueError("No segment data to concatenate")

    # Use first segment header as the canonical header
    first_header = segment_blobs[0][:WAV_HEADER_SIZE]

    # Parse sample_rate from bytes 24-27 (little-endian uint32)
    sample_rate = struct.unpack_from("<I", first_header, 24)[0]
    # Parse bits-per-sample (bytes 34-35) and channels (bytes 22-23) for duration calc
    bits_per_sample = struct.unpack_from("<H", first_header, 34)[0]
    num_channels = struct.unpack_from("<H", first_header, 22)[0]

    # Collect raw PCM data: full first segment after header, rest stripped
    pcm_chunks: List[bytes] = []
    pcm_chunks.append(segment_blobs[0][WAV_HEADER_SIZE:])
    for blob in segment_blobs[1:]:
        pcm_chunks.append(blob[WAV_HEADER_SIZE:])

    total_data_size = sum(len(c) for c in pcm_chunks)

    # Rewrite RIFF size (bytes 4-7) and data chunk size (bytes 40-43)
    header = bytearray(first_header)
    struct.pack_into("<I", header, 4, 36 + total_data_size)  # RIFF size
    struct.pack_into("<I", header, 40, total_data_size)       # data size

    concatenated = bytes(header) + b"".join(pcm_chunks)

    # Compute duration
    bytes_per_sample = max(bits_per_sample // 8, 1)
    bytes_per_second = sample_rate * num_channels * bytes_per_sample
    duration = total_data_size / bytes_per_second if bytes_per_second > 0 else 0.0

    return concatenated, duration


def _cleanup_segments(storage, user_id: int, storage_id: int, session_uid: str) -> int:
    """Delete all segment files for a session. Returns count of files deleted."""
    prefix = f"recordings/{user_id}/{storage_id}/segments/{session_uid}_seg_"
    keys = storage.list_files(prefix)
    deleted = 0
    for key in keys:
        try:
            storage.delete_file(key)
            deleted += 1
        except Exception as e:
            logger.warning(f"Failed to delete segment {key}: {e}")
    return deleted


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/internal/recordings/upload", status_code=201, include_in_schema=False)
async def internal_upload_recording(
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(default=None),
    session_uid: Optional[str] = Form(default=None),
    media_type: str = Form(default="audio"),
    media_format: str = Form(default="wav"),
    duration_seconds: Optional[float] = Form(default=None),
    sample_rate: Optional[int] = Form(default=None),
    is_final: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
):
    segment_index: Optional[int] = None

    if metadata:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="Invalid JSON in metadata")
        session_uid = session_uid or meta.get("session_uid")
        media_type = meta.get("media_type", media_type)
        media_format = meta.get("format", media_format)
        duration_seconds = meta.get("duration_seconds", duration_seconds)
        sample_rate = meta.get("sample_rate", sample_rate)
        if "is_final" in meta:
            is_final = _to_bool(meta.get("is_final"), default=True)
        if "segment_index" in meta:
            raw_seg = meta.get("segment_index")
            segment_index = int(raw_seg) if raw_seg is not None else None

    if not session_uid:
        raise HTTPException(status_code=422, detail="session_uid is required")

    session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
    meeting_session = (await db.execute(session_stmt)).scalars().first()

    if not meeting_session:
        if not is_final:
            return {"status": "pending", "detail": f"Meeting session not ready yet: {session_uid}"}
        raise HTTPException(status_code=404, detail=f"Meeting session not found: {session_uid}")

    meeting = await db.get(Meeting, meeting_session.meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting not found for session: {session_uid}")

    user_id = meeting.user_id
    file_data = await file.read()
    file_size = len(file_data)

    use_meeting_data = get_recording_metadata_mode() == "meeting_data"
    meeting_data_dict = dict(meeting.data or {}) if use_meeting_data else {}
    recordings_list = list(meeting_data_dict.get("recordings") or []) if use_meeting_data else []
    existing_rec = None
    existing_idx = None
    legacy_id = _new_recording_numeric_id() if use_meeting_data else None

    if use_meeting_data:
        for idx, rec in enumerate(recordings_list):
            if isinstance(rec, dict) and rec.get("session_uid") == session_uid and rec.get("source") == RecordingSource.BOT.value:
                existing_rec = rec
                existing_idx = idx
                legacy_id = rec.get("id") or legacy_id
                break

    recording = None
    if not use_meeting_data:
        recording = Recording(meeting_id=meeting.id, user_id=user_id, session_uid=session_uid, source="bot", status="uploading")
        db.add(recording)
        await db.flush()
    storage_id = legacy_id if use_meeting_data else recording.id

    # Determine whether this is a segmented upload
    is_segmented = segment_index is not None and segment_index >= 0
    is_final_signal_only = segment_index is not None and segment_index == -1

    content_types = {"wav": "audio/wav", "webm": "video/webm", "opus": "audio/opus", "mp3": "audio/mpeg", "jpg": "image/jpeg", "png": "image/png"}
    content_type = content_types.get(media_format, "application/octet-stream")
    final_storage_path = f"recordings/{user_id}/{storage_id}/{session_uid}.{media_format}"
    storage = get_storage_client()

    # ---- Segmented upload path ----
    if is_segmented or is_final_signal_only:
        # Upload the segment file (unless this is the -1 sentinel with no real data)
        if is_segmented and file_size > 0:
            seg_path = _segment_storage_path(user_id, storage_id, session_uid, segment_index, media_format)
            try:
                storage.upload_file(seg_path, file_data, content_type=content_type)
                logger.info(f"Uploaded segment {segment_index} for {session_uid} ({file_size} bytes)")
            except Exception as e:
                logger.error(f"Segment upload failed for {session_uid} seg {segment_index}: {e}", exc_info=True)
                if recording:
                    recording.status = "failed"
                    recording.error_message = str(e)
                    await db.commit()
                raise HTTPException(status_code=500, detail="Failed to upload segment to storage")

        # If not final, update in-progress status and return
        if not is_final:
            if use_meeting_data:
                existing_media = (existing_rec.get("media_files", [{}])[0] if existing_rec else {})
                media_file_id = existing_media.get("id") or _new_recording_numeric_id()
                created_at = existing_rec.get("created_at") if existing_rec else datetime.utcnow().isoformat()
                rec_payload = {
                    "id": legacy_id,
                    "meeting_id": meeting.id,
                    "user_id": user_id,
                    "session_uid": session_uid,
                    "source": RecordingSource.BOT.value,
                    "status": RecordingStatus.IN_PROGRESS.value,
                    "created_at": created_at,
                    "completed_at": None,
                    "media_files": [],
                }
                if existing_idx is None:
                    recordings_list.append(rec_payload)
                else:
                    recordings_list[existing_idx] = rec_payload
                meeting_data_dict["recordings"] = recordings_list
                meeting.data = meeting_data_dict
                attributes.flag_modified(meeting, "data")
                await db.commit()
            return {
                "status": "segment_uploaded",
                "segment_index": segment_index,
                "session_uid": session_uid,
                "storage_id": storage_id,
            }

        # ---- is_final=True: concatenate all segments ----
        try:
            # For the -1 sentinel, no new segment data to append;
            # for a real final segment, it was already uploaded above so pass None
            concatenated_data, concat_duration = _concatenate_wav_segments(
                storage, user_id, storage_id, session_uid, media_format,
                final_segment_data=None,
            )
            concat_size = len(concatenated_data)
            # Parse sample_rate from the concatenated header for metadata
            concat_sample_rate = struct.unpack_from("<I", concatenated_data, 24)[0]

            # Upload the final concatenated file
            storage.upload_file(final_storage_path, concatenated_data, content_type=content_type)
            logger.info(f"Concatenated {session_uid}: {concat_size} bytes, {concat_duration:.2f}s -> {final_storage_path}")

            # Clean up segment files only after successful concatenation
            try:
                deleted_count = _cleanup_segments(storage, user_id, storage_id, session_uid)
                logger.info(f"Cleaned up {deleted_count} segment files for {session_uid}")
            except Exception as e:
                logger.warning(f"Segment cleanup failed for {session_uid} (segments kept as backup): {e}")

            # Use concatenated values for the final metadata
            file_size = concat_size
            duration_seconds = concat_duration
            sample_rate = concat_sample_rate
            file_data = concatenated_data

        except Exception as e:
            logger.error(f"Segment concatenation failed for {session_uid}: {e}", exc_info=True)
            # Don't delete segments — they serve as backup
            if recording:
                recording.status = "failed"
                recording.error_message = f"Concatenation failed: {e}"
                await db.commit()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to concatenate recording segments: {e}",
            )

        storage_path = final_storage_path

    # ---- Legacy single-file upload path ----
    else:
        storage_path = final_storage_path
        try:
            storage.upload_file(storage_path, file_data, content_type=content_type)
        except Exception as e:
            logger.error(f"Storage upload failed for {session_uid}: {e}", exc_info=True)
            if recording:
                recording.status = "failed"
                recording.error_message = str(e)
                await db.commit()
            raise HTTPException(status_code=500, detail="Failed to upload recording to storage")

    # ---- Persist metadata (common for both paths) ----

    if use_meeting_data:
        existing_media = (existing_rec.get("media_files", [{}])[0] if existing_rec else {})
        media_file_id = existing_media.get("id") or _new_recording_numeric_id()
        created_at = existing_rec.get("created_at") if existing_rec else datetime.utcnow().isoformat()
        rec_payload = {
            "id": legacy_id,
            "meeting_id": meeting.id,
            "user_id": user_id,
            "session_uid": session_uid,
            "source": RecordingSource.BOT.value,
            "status": RecordingStatus.COMPLETED.value if is_final else RecordingStatus.IN_PROGRESS.value,
            "created_at": created_at,
            "completed_at": datetime.utcnow().isoformat() if is_final else None,
            "media_files": [{
                "id": media_file_id,
                "type": media_type,
                "format": media_format,
                "storage_path": storage_path,
                "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
                "file_size_bytes": file_size,
                "duration_seconds": duration_seconds,
                "metadata": {"sample_rate": sample_rate} if sample_rate else {},
                "created_at": datetime.utcnow().isoformat(),
            }],
        }
        if existing_idx is None:
            recordings_list.append(rec_payload)
        else:
            recordings_list[existing_idx] = rec_payload
        meeting_data_dict["recordings"] = recordings_list
        meeting.data = meeting_data_dict
        attributes.flag_modified(meeting, "data")
        await db.commit()
        if is_final:
            asyncio.create_task(send_event_webhook(meeting.id, "recording.completed", {"recording": rec_payload}))
        return {"recording_id": rec_payload["id"], "media_file_id": media_file_id, "storage_path": storage_path, "status": rec_payload["status"]}

    # DB mode
    file_metadata = {}
    if sample_rate:
        file_metadata["sample_rate"] = sample_rate
    media_file = MediaFile(
        recording_id=recording.id,
        type=media_type,
        format=media_format,
        storage_path=storage_path,
        storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
        file_size_bytes=file_size,
        duration_seconds=duration_seconds,
        extra_metadata=file_metadata if file_metadata else {},
    )
    db.add(media_file)
    recording.status = "completed"
    recording.completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(recording)
    await db.refresh(media_file)

    asyncio.create_task(send_event_webhook(meeting.id, "recording.completed", {
        "recording": {"id": recording.id, "meeting_id": recording.meeting_id, "session_uid": session_uid, "status": recording.status, "media_file_id": media_file.id, "file_size_bytes": file_size, "media_type": media_type, "media_format": media_format}
    }))
    return {"recording_id": recording.id, "media_file_id": media_file.id, "storage_path": storage_path, "status": recording.status}


@router.get("/recordings", response_model=RecordingListResponse, summary="List recordings for the authenticated user")
async def list_recordings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    meeting_id: Optional[int] = Query(default=None),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        recs = await _list_meeting_data_recordings(db, user.id, meeting_id=meeting_id)
        page = recs[offset:offset + limit]
        return RecordingListResponse(recordings=[RecordingResponse.model_validate(r) for r in page])

    stmt = select(Recording).where(Recording.user_id == user.id)
    if meeting_id is not None:
        stmt = stmt.where(Recording.meeting_id == meeting_id)
    stmt = stmt.order_by(desc(Recording.created_at)).offset(offset).limit(limit)
    result = await db.execute(stmt)
    recordings = result.scalars().all()
    items = []
    for rec in recordings:
        await db.refresh(rec, ["media_files"])
        items.append(RecordingResponse.model_validate(rec))
    return RecordingListResponse(recordings=items)


@router.get("/recordings/{recording_id}", response_model=RecordingResponse, summary="Get a single recording")
async def get_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return RecordingResponse.model_validate(rec)

    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    await db.refresh(recording, ["media_files"])
    return RecordingResponse.model_validate(recording)


@router.get("/recordings/{recording_id}/media/{media_file_id}/download", summary="Get download URL for a media file")
async def download_media_file(
    recording_id: int, media_file_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    content_type_map = {"wav": "audio/wav", "webm": "video/webm", "opus": "audio/opus", "mp3": "audio/mpeg", "jpg": "image/jpeg", "png": "image/png"}

    if get_recording_metadata_mode() == "meeting_data":
        _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        mf = None
        for f in rec.get("media_files") or []:
            if int(f.get("id", -1)) == media_file_id:
                mf = f
                break
        if not mf:
            raise HTTPException(status_code=404, detail="Media file not found")
        fmt = str(mf.get("format", "bin")).lower()
        ct = content_type_map.get(fmt, "application/octet-stream")
        if mf.get("storage_backend") == "local":
            url = f"/recordings/{recording_id}/media/{media_file_id}/raw"
        else:
            url = get_storage_client().get_presigned_url(mf["storage_path"], expires=3600)
        return {"download_url": url, "filename": f"{recording_id}_{mf.get('type', 'audio')}.{fmt}", "content_type": ct, "file_size_bytes": mf.get("file_size_bytes")}

    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    stmt = select(MediaFile).where(and_(MediaFile.id == media_file_id, MediaFile.recording_id == recording_id))
    mf = (await db.execute(stmt)).scalars().first()
    if not mf:
        raise HTTPException(status_code=404, detail="Media file not found")
    ct = content_type_map.get(mf.format.lower(), "application/octet-stream")
    if mf.storage_backend == "local":
        url = f"/recordings/{recording_id}/media/{media_file_id}/raw"
    else:
        url = get_storage_client().get_presigned_url(mf.storage_path, expires=3600)
    return {"download_url": url, "filename": f"{mf.recording_id}_{mf.type}.{mf.format}", "content_type": ct, "file_size_bytes": mf.file_size_bytes}


@router.get("/recordings/{recording_id}/media/{media_file_id}/raw", summary="Download media file content")
async def download_media_file_raw(
    recording_id: int, media_file_id: int,
    request: Request,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    content_type_map = {"wav": "audio/wav", "webm": "video/webm", "opus": "audio/opus", "mp3": "audio/mpeg", "jpg": "image/jpeg", "png": "image/png"}

    # Resolve the storage path and content type
    storage_path = None
    ct = "application/octet-stream"
    filename = ""

    if get_recording_metadata_mode() == "meeting_data":
        _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        for f in rec.get("media_files") or []:
            if int(f.get("id", -1)) == media_file_id:
                storage_path = f.get("storage_path")
                fmt = str(f.get("format", "bin")).lower()
                ct = content_type_map.get(fmt, ct)
                filename = f"{recording_id}_{f.get('type', 'audio')}.{fmt}"
                break
    else:
        recording = await db.get(Recording, recording_id)
        if not recording or recording.user_id != user.id:
            raise HTTPException(status_code=404, detail="Recording not found")
        stmt = select(MediaFile).where(and_(MediaFile.id == media_file_id, MediaFile.recording_id == recording_id))
        mf = (await db.execute(stmt)).scalars().first()
        if mf:
            storage_path = mf.storage_path
            ct = content_type_map.get(mf.format.lower(), ct)
            filename = f"{mf.recording_id}_{mf.type}.{mf.format}"

    if not storage_path:
        raise HTTPException(status_code=404, detail="Media file not found")

    try:
        data = get_storage_client().download_file(storage_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file content not found in storage")
    except Exception as e:
        logger.error(f"Failed to download media file {media_file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read media file")

    headers = {"Content-Disposition": f'inline; filename="{filename}"', "Accept-Ranges": "bytes"}

    # Range request support
    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        total = len(data)
        spec = range_header[6:].strip()
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else total - int(end_s)
        end = int(end_s) if end_s and start_s else total - 1
        end = min(end, total - 1)
        chunk = data[start:end + 1]
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(len(chunk))
        return Response(content=chunk, media_type=ct, status_code=206, headers=headers)

    return Response(content=data, media_type=ct, headers=headers)


@router.delete("/recordings/{recording_id}", summary="Delete a recording and its media files")
async def delete_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    if get_recording_metadata_mode() == "meeting_data":
        meeting, rec = await _find_meeting_data_recording(db, user.id, recording_id)
        if meeting is None or rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        storage = get_storage_client()
        for mf in rec.get("media_files") or []:
            path = mf.get("storage_path")
            if path:
                try:
                    storage.delete_file(path)
                except Exception as e:
                    logger.warning(f"Failed to delete {path}: {e}")
        current = dict(meeting.data or {})
        current["recordings"] = [r for r in (current.get("recordings") or []) if not (isinstance(r, dict) and int(r.get("id", -1)) == recording_id)]
        meeting.data = current
        attributes.flag_modified(meeting, "data")
        await db.commit()
        return {"status": "deleted", "recording_id": recording_id}

    recording = await db.get(Recording, recording_id)
    if not recording or recording.user_id != user.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    await db.refresh(recording, ["media_files"])
    storage = get_storage_client()
    for mf in recording.media_files:
        try:
            storage.delete_file(mf.storage_path)
        except Exception as e:
            logger.warning(f"Failed to delete {mf.storage_path}: {e}")
    await db.delete(recording)
    await db.commit()
    return {"status": "deleted", "recording_id": recording_id}
