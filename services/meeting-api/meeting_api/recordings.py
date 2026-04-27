"""/recordings/* and /internal/recordings/upload endpoints.

Recording management — /recordings/* and /internal/recordings/upload endpoints.
"""

import asyncio
import json
import logging
import os
import uuid as uuid_lib
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    # Incremental-upload support (Pack B / issue #218). When the bot uploads
    # per-chunk, `chunk_seq` is incremented per chunk and `is_final` stays
    # False until the last one. Each chunk gets its own object in MinIO under
    # a per-session prefix; each creates a distinct `media_files[]` entry
    # (meeting_data mode) or MediaFile row (DB mode). Recording.status stays
    # IN_PROGRESS until is_final=True — that's the "partial recording" marker.
    # Legacy one-shot callers that don't pass chunk_seq default to 0 with
    # is_final=True; behavior is byte-identical to today for that path.
    chunk_seq: int = Form(default=0),
    db: AsyncSession = Depends(get_db),
):
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
        if "chunk_seq" in meta:
            try:
                chunk_seq = int(meta.get("chunk_seq"))
            except (TypeError, ValueError):
                pass

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
        # Incremental-upload aware: look up an existing Recording for this
        # session_uid first. Chunks 1..N reuse it; chunk 0 creates it. The
        # one-shot legacy path lands here with chunk_seq=0 and no prior
        # row — same as before.
        existing_stmt = select(Recording).where(
            Recording.meeting_id == meeting.id,
            Recording.session_uid == session_uid,
            Recording.source == "bot",
        )
        recording = (await db.execute(existing_stmt)).scalars().first()
        if recording is None:
            recording = Recording(meeting_id=meeting.id, user_id=user_id, session_uid=session_uid, source="bot", status="uploading")
            db.add(recording)
            await db.flush()
    storage_id = legacy_id if use_meeting_data else recording.id

    # Incremental-upload storage path: per-session + per-media-type directory
    # + zero-padded chunk index. media_type is part of the path because audio
    # chunks and a video blob often share the same format (webm) — without
    # the type prefix they'd collide on chunk_seq=0, and the second upload
    # would silently overwrite the first (Bug C 2026-04-21: dashboard
    # showed video-player UI but the MinIO object was an audio-only blob
    # because audio overwrote video at .../000000.webm).
    storage_path = f"recordings/{user_id}/{storage_id}/{session_uid}/{media_type}/{chunk_seq:06d}.{media_format}"
    content_types = {"wav": "audio/wav", "webm": "video/webm", "opus": "audio/opus", "mp3": "audio/mpeg", "jpg": "image/jpeg", "png": "image/png"}
    content_type = content_types.get(media_format, "application/octet-stream")

    try:
        storage = get_storage_client()
        storage.upload_file(storage_path, file_data, content_type=content_type)
    except Exception as e:
        logger.error(f"Storage upload failed for {session_uid}: {e}", exc_info=True)
        if recording:
            recording.status = "failed"
            recording.error_message = str(e)
            await db.commit()
        raise HTTPException(status_code=500, detail="Failed to upload recording to storage")

    if use_meeting_data:
        # Media-file materialization policy (Bug C+D fix, 2026-04-21):
        #   - Intermediate chunks (is_final=false) upload to MinIO for
        #     SIGKILL safety but do NOT append media_files entries.
        #   - The final chunk (is_final=true) + every distinct media_type
        #     (audio, video) yields ONE media_files entry per type pointing
        #     at the final-chunk storage_path.
        #   - This gives the dashboard ONE audio entry + (optionally) ONE
        #     video entry per recording, instead of N per-chunk entries
        #     that confused the fragment player and the seek-to-segment
        #     math. Per-chunk history is still reconstructable from the
        #     MinIO listing under the session/type prefix.
        if existing_rec is None:
            rec_payload = {
                "id": legacy_id,
                "meeting_id": meeting.id,
                "user_id": user_id,
                "session_uid": session_uid,
                "source": RecordingSource.BOT.value,
                "status": RecordingStatus.COMPLETED.value if is_final else RecordingStatus.IN_PROGRESS.value,
                "created_at": datetime.utcnow().isoformat(),
                "completed_at": datetime.utcnow().isoformat() if is_final else None,
                "media_files": [],
            }
            existing_idx = len(recordings_list)
            recordings_list.append(rec_payload)
            was_completed = False
        else:
            rec_payload = dict(existing_rec)
            was_completed = rec_payload.get("status") == RecordingStatus.COMPLETED.value

        status_transitioned_to_completed = False
        # v0.10.5 Pack E.1.a — per-chunk durable media_files write (#268
        # reproduction 2026-04-27).
        #
        # Pre-Pack-E.1.a: intermediate chunks went to MinIO/S3 but media_files
        # was only updated on is_final=true. When the bot died mid-active
        # (e.g. Playwright Execution context destroyed in production today),
        # 31 chunks landed in S3 with media_files=[] in DB — orphaned.
        #
        # Now: every successful chunk upload UPDATES the per-type media_files
        # entry with the latest chunk's metadata. Dashboard still sees ONE
        # entry per type (audio, video) — UX unchanged from prior shape;
        # what changes is the entry exists from chunk_seq=0 onward instead
        # of waiting for is_final. Even mid-stream death leaves a partial
        # recording row with `status=IN_PROGRESS` + the latest chunk's
        # metadata, so the dashboard can show "Recording in progress (X
        # chunks captured)" rather than "no recording."
        existing_media_files = [
            mf for mf in (rec_payload.get("media_files") or [])
            if mf.get("type") != media_type
        ]
        existing_media_files.append({
            "id": _new_recording_numeric_id(),
            "type": media_type,
            "format": media_format,
            "storage_path": storage_path,
            "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
            "file_size_bytes": file_size,
            "duration_seconds": duration_seconds,
            "chunk_seq": chunk_seq,
            "metadata": {"sample_rate": sample_rate} if sample_rate else {},
            "created_at": datetime.utcnow().isoformat(),
            "is_final": is_final,
        })
        rec_payload["media_files"] = existing_media_files
        if is_final:
            rec_payload["status"] = RecordingStatus.COMPLETED.value
            rec_payload["completed_at"] = datetime.utcnow().isoformat()
            status_transitioned_to_completed = not was_completed
        else:
            # Intermediate chunk: keep IN_PROGRESS but durably-write the
            # latest media_files entry so dashboard shows partial recording.
            rec_payload["status"] = RecordingStatus.IN_PROGRESS.value
        recordings_list[existing_idx] = rec_payload
        meeting_data_dict["recordings"] = recordings_list
        meeting.data = meeting_data_dict
        attributes.flag_modified(meeting, "data")
        await db.commit()
        if status_transitioned_to_completed:
            asyncio.create_task(send_event_webhook(meeting.id, "recording.completed", {"recording": rec_payload}))
        final_media = rec_payload.get("media_files") or []
        mf_id = final_media[-1]["id"] if (is_final and final_media) else None
        return {"recording_id": rec_payload["id"], "media_file_id": mf_id, "storage_path": storage_path, "status": rec_payload["status"], "chunk_seq": chunk_seq}

    # DB mode — same materialization policy as meeting_data mode (Bug C+D fix):
    # intermediate chunks upload to MinIO for SIGKILL safety but do NOT create
    # a MediaFile row. On is_final=true, one MediaFile row per media_type is
    # created (replacing any earlier same-type row for this Recording).
    was_completed = recording.status == "completed"
    media_file = None
    if is_final:
        # Retire any existing MediaFile for this recording + media_type.
        from sqlalchemy import delete
        await db.execute(
            delete(MediaFile).where(
                MediaFile.recording_id == recording.id,
                MediaFile.type == media_type,
            )
        )
        file_metadata = {"chunk_seq": chunk_seq}
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
            extra_metadata=file_metadata,
        )
        db.add(media_file)
        recording.status = "completed"
        recording.completed_at = datetime.utcnow()
    else:
        # Keep in-progress; never downgrade from completed → uploading if a
        # late chunk arrives after a final.
        if not was_completed:
            recording.status = "uploading"
    await db.commit()
    await db.refresh(recording)
    if media_file:
        await db.refresh(media_file)

    if is_final and not was_completed:
        asyncio.create_task(send_event_webhook(meeting.id, "recording.completed", {
            "recording": {"id": recording.id, "meeting_id": recording.meeting_id, "session_uid": session_uid, "status": recording.status, "media_file_id": (media_file.id if media_file else None), "file_size_bytes": file_size, "media_type": media_type, "media_format": media_format}
        }))
    return {"recording_id": recording.id, "media_file_id": (media_file.id if media_file else None), "storage_path": storage_path, "status": recording.status, "chunk_seq": chunk_seq}


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
