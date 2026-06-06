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
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .database import get_db
from .models import Meeting, MeetingSession
from .schemas import (
    RecordingResponse,
    RecordingListResponse,
    RecordingStatus,
    RecordingSource,
)
from .storage import create_storage_client

from .auth import get_user_and_token
from .collector.processors import verify_meeting_token
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


async def require_recording_upload_token(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Missing recording upload token")
    token_claims = verify_meeting_token(auth.removeprefix("Bearer ").strip())
    if not token_claims:
        raise HTTPException(status_code=403, detail="Invalid recording upload token")
    return token_claims


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


def media_content_type(media_type: str, media_format: str) -> str:
    fmt = str(media_format or "").lower()
    typ = str(media_type or "").lower()
    if fmt == "webm":
        return "audio/webm" if typ == "audio" else "video/webm"
    content_types = {
        "wav": "audio/wav",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
    }
    return content_types.get(fmt, "application/octet-stream")


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
    token_claims: dict = Depends(require_recording_upload_token),
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
    # in meeting.data.recordings[].media_files. Recording status stays
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

    if not isinstance(token_claims, dict):
        # Direct unit tests call the endpoint function without FastAPI dependency injection.
        token_claims = {"meeting_id": meeting_session.meeting_id}

    if int(token_claims.get("meeting_id")) != int(meeting_session.meeting_id):
        raise HTTPException(status_code=403, detail="Recording token does not match meeting")

    meeting = await db.get(Meeting, meeting_session.meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting not found for session: {session_uid}")

    user_id = meeting.user_id
    # TODO(v0.10.7): bound UploadFile body size for defense-in-depth against
    # compromised internal callers. Route is JWT-authed via
    # require_recording_upload_token (HS256, aud/iss/scope/exp) and meeting-api
    # is on Docker `expose:` not `ports:` — not externally reachable today.
    file_data = await file.read()
    file_size = len(file_data)

    meeting_data_dict = dict(meeting.data or {})
    recordings_list = list(meeting_data_dict.get("recordings") or [])
    existing_rec = None
    existing_idx = None
    recording_id = _new_recording_numeric_id()

    for idx, rec in enumerate(recordings_list):
        if isinstance(rec, dict) and rec.get("session_uid") == session_uid and rec.get("source") == RecordingSource.BOT.value:
            existing_rec = rec
            existing_idx = idx
            recording_id = rec.get("id") or recording_id
            break

    storage_id = recording_id

    # Incremental-upload storage path: per-session + per-media-type directory
    # + zero-padded chunk index. media_type is part of the path because audio
    # chunks and a video blob often share the same format (webm) — without
    # the type prefix they'd collide on chunk_seq=0, and the second upload
    # would silently overwrite the first (Bug C 2026-04-21: dashboard
    # showed video-player UI but the MinIO object was an audio-only blob
    # because audio overwrote video at .../000000.webm).
    storage_path = f"recordings/{user_id}/{storage_id}/{session_uid}/{media_type}/{chunk_seq:06d}.{media_format}"
    content_type = media_content_type(media_type, media_format)

    try:
        storage = get_storage_client()
        storage.upload_file(storage_path, file_data, content_type=content_type)
    except Exception as e:
        logger.error(f"Storage upload failed for {session_uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to upload recording to storage")

    # JSONB-only path (v0.10.6.1): write recording metadata into meeting.data.
    # Media-file materialization policy:
    #   - Every successful chunk upload UPDATES the per-type media_files entry
    #     with the latest chunk's metadata (one entry per media_type per
    #     recording — see Pack E.1.a history).
    #   - Concurrency: SELECT ... FOR UPDATE held BEFORE we snapshot
    #     meeting.data, so concurrent audio+video uploads serialize on the
    #     meeting row and don't lose each other's media_files entries.
    from sqlalchemy import select as _sql_select
    locked_meeting = (await db.execute(
        _sql_select(Meeting)
        .where(Meeting.id == meeting_session.meeting_id)
        .with_for_update()
    )).scalar_one()
    meeting = locked_meeting
    meeting_data_dict = dict(meeting.data or {})
    recordings_list = list(meeting_data_dict.get("recordings") or [])
    # Re-find existing_rec under the FRESH (locked) snapshot. Adopt the
    # canonical recording_id from the existing rec if present.
    existing_rec = None
    existing_idx = None
    for idx, rec in enumerate(recordings_list):
        if isinstance(rec, dict) and rec.get("session_uid") == session_uid and rec.get("source") == RecordingSource.BOT.value:
            existing_rec = rec
            existing_idx = idx
            recording_id = rec.get("id") or recording_id
            break

    if existing_rec is None:
        rec_payload = {
            "id": recording_id,
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
    prior_media_files = list(rec_payload.get("media_files") or [])
    prior_types = {mf.get("type") for mf in prior_media_files}
    chunk_action = "appended" if media_type not in prior_types else "in_place"
    prior_same_type = next(
        (mf for mf in prior_media_files if mf.get("type") == media_type),
        None,
    )
    prior_bytes = int((prior_same_type or {}).get("file_size_bytes") or 0) if prior_same_type else 0
    prior_chunk_count = int((prior_same_type or {}).get("chunk_count") or (1 if prior_same_type else 0))
    prior_first_chunk_at = (prior_same_type or {}).get("first_chunk_at") if prior_same_type else None
    cumulative_bytes = (prior_bytes + file_size) if prior_same_type else file_size
    cumulative_chunk_count = (prior_chunk_count + 1) if prior_same_type else 1
    first_chunk_at = prior_first_chunk_at or datetime.utcnow().isoformat()
    existing_media_files = [
        mf for mf in prior_media_files
        if mf.get("type") != media_type
    ]
    # Pack U.7 — preserve master path against late-chunk overwrite.
    prior_sp = (prior_same_type or {}).get("storage_path") or ""
    prior_is_final = bool((prior_same_type or {}).get("is_final"))
    master_finalized = (
        prior_sp.endswith("/audio/master.webm")
        or prior_sp.endswith("/audio/master.wav")
        or prior_is_final
    )
    new_storage_path = prior_sp if master_finalized else storage_path
    new_is_final = True if master_finalized else is_final
    if master_finalized and not is_final:
        logger.warning(
            "[E1A] late_chunk_after_finalize meeting_id=%s recording_id=%s media_type=%s "
            "chunk_seq=%s — preserving master storage_path=%s",
            meeting.id, recording_id, media_type, chunk_seq, prior_sp,
        )
    existing_media_files.append({
        "id": (prior_same_type or {}).get("id") or _new_recording_numeric_id(),
        "type": media_type,
        "format": media_format,
        "storage_path": new_storage_path,
        "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
        "file_size_bytes": cumulative_bytes,
        "last_chunk_size_bytes": file_size,
        "chunk_count": cumulative_chunk_count,
        "duration_seconds": duration_seconds,
        "chunk_seq": chunk_seq,
        "first_chunk_at": first_chunk_at,
        "metadata": {"sample_rate": sample_rate} if sample_rate else {},
        "created_at": datetime.utcnow().isoformat(),
        "is_final": new_is_final,
        "finalized_at": (prior_same_type or {}).get("finalized_at"),
        "finalized_by": (prior_same_type or {}).get("finalized_by"),
    })
    rec_payload["media_files"] = existing_media_files
    logger.info(
        "[E1A] chunk_write meeting_id=%s recording_id=%s media_type=%s "
        "chunk_seq=%s prior_chunks=%s action=%s is_final=%s",
        meeting.id, rec_payload.get("id"), media_type,
        chunk_seq, prior_chunk_count, chunk_action, is_final,
    )
    if is_final:
        rec_payload["status"] = RecordingStatus.COMPLETED.value
        rec_payload["completed_at"] = datetime.utcnow().isoformat()
        status_transitioned_to_completed = not was_completed
    else:
        # v0.10.5 R2 — defense-in-depth: terminal state is sticky; never downgrade COMPLETED → IN_PROGRESS
        # if a stray late chunk arrives after reconciler finalization.
        if not was_completed:
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


@router.get("/recordings", response_model=RecordingListResponse, summary="List recordings for the authenticated user")
async def list_recordings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    meeting_id: Optional[int] = Query(default=None),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    recs = await _list_meeting_data_recordings(db, user.id, meeting_id=meeting_id)
    page = recs[offset:offset + limit]
    return RecordingListResponse(recordings=[RecordingResponse.model_validate(r) for r in page])


@router.get("/recordings/{recording_id}", response_model=RecordingResponse, summary="Get a single recording")
async def get_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return RecordingResponse.model_validate(rec)


@router.get("/recordings/{recording_id}/master", summary="Get presigned URL for the canonical master media file of a given type")
async def get_recording_master(
    recording_id: int,
    type: str = Query(..., regex="^(audio|video)$", description="Media type: audio | video"),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """v0.10.6.1 — Canonical playback endpoint (ADR-2).

    The dashboard reads `recording.playback_url.audio` (or `.video`) from
    the meeting payload — a stable route. Hitting this endpoint resolves
    the route to the master media file's presigned URL on each call.

    Producer-writes / consumer-reads model: `recording_finalizer` writes
    `playback_url` onto the JSONB recording element once master assembly
    completes. The dashboard reads it. Selection logic
    (`pickMasterMediaFile()`) is deleted; the dashboard no longer reasons
    about which media_files[] entry is the master.

    Returns 404 when no master exists yet for the requested type (meeting
    still in progress, finalizer crashed, no-such-type recording). The
    dashboard renders "finalizing" on 404 — explicit state, NOT a silent
    fallback (principle 5).
    """
    _, user = auth

    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    # Find the master media file of the requested type. Master =
    # finalized_by == "recording_finalizer.master".
    master_mf = None
    for mf in rec.get("media_files") or []:
        if mf.get("type") == type and mf.get("finalized_by") == "recording_finalizer.master":
            master_mf = mf
            break
    if not master_mf:
        raise HTTPException(status_code=404, detail=f"No master {type} file for recording {recording_id} (still finalizing or not produced)")
    media_file_id = master_mf.get("id")
    if media_file_id is None:
        raise HTTPException(status_code=404, detail="Master media file id missing")

    # Delegate to the existing per-id endpoint logic, then enrich with
    # duration_seconds (v0.10.6.1 Task 9). Dashboard reads duration from
    # the master response directly so it no longer needs to peek into
    # media_files[] for duration.
    response = await download_media_file(recording_id, media_file_id, auth, db)
    response["media_file_id"] = media_file_id
    response["raw_url"] = f"/recordings/{recording_id}/media/{media_file_id}/raw"
    response["duration_seconds"] = master_mf.get("duration_seconds")
    return response


@router.get("/recordings/{recording_id}/media/{media_file_id}/download", summary="Get presigned download URL for a media file")
async def download_media_file(
    recording_id: int, media_file_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Return a short-lived presigned URL pointing at the master media file in MinIO.

    Pack U.8 (v0.10.6) contract:
    - After Pack U.5+U.6, `recording_finalizer` builds a single
      `<prefix>/master.{webm|wav}` server-side at bot_exit_callback and
      rewrites `media_file.storage_path` to point at it. This endpoint
      hands the dashboard a 1-hour presigned URL to that master so the
      browser can stream directly from MinIO with native HTTP Range
      (no in-process proxying through meeting-api).
    - Option B chosen: return HTTP 200 + JSON `{"url": "<presigned>", ...}`
      rather than a 302 redirect. Keeps the API stable and lets the
      dashboard control the `<audio>` lifecycle (preload, autoplay).
    - TTL: 3600s (1 hour). Long enough for browser playback even on long
      meetings, short enough to limit credential-leak blast radius if
      the URL escapes the dashboard session.
    - `local` storage backend (dev-only) cannot mint presigned URLs; the
      response surfaces a `/raw` fallback path (still proxied in-process).
      For `minio`/`s3` backends, returns the presigned URL directly.
    - 404 when the master file does not yet exist (meeting still in
      progress, finalizer crashed, etc.). Callers MUST handle this.
    """
    _, user = auth

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
    ct = media_content_type(str(mf.get("type", "audio")), fmt)
    storage_path = mf.get("storage_path")
    storage_backend = mf.get("storage_backend")
    type_label = mf.get("type", "audio")
    file_size = mf.get("file_size_bytes")

    if not storage_path:
        raise HTTPException(status_code=404, detail="Media file storage path not set")

    storage = get_storage_client()
    # Master may not exist yet: meeting still in progress, or finalizer
    # crashed before producing the concatenated master. Surface a 404 so
    # the dashboard can fall back to /raw (Pack P: this is the LAST
    # allowed fallback in the playback path until master_ready flag exists).
    try:
        master_present = storage.file_exists(storage_path)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"file_exists check failed for {storage_path}: {e}")
        master_present = False
    if not master_present:
        raise HTTPException(status_code=404, detail="Media file content not found in storage")

    if storage_backend == "local":
        # Local backend can't mint presigned URLs (no signed-URL semantics
        # on filesystem). Fall back to the legacy /raw proxy path. This is
        # an explicit per-deployment decision (Pack P), not a runtime
        # fallback — local storage is dev-only.
        url = f"/recordings/{recording_id}/media/{media_file_id}/raw"
    else:
        url = storage.get_presigned_url(storage_path, expires=3600)

    return {
        "url": url,
        "download_url": url,  # legacy alias kept for back-compat with v0.10.5 clients
        "filename": f"{recording_id}_{type_label}.{fmt}",
        "content_type": ct,
        "file_size_bytes": file_size,
        "expires_in": 3600,
    }


# Legacy: in-process proxy through meeting-api. Kept for back-compat with
# clients pre-Pack U.8 (v0.10.6). The new playback path uses /download +
# presigned URLs so the browser streams directly from MinIO with native
# HTTP Range. /raw remains as the LAST allowed fallback when /download
# returns 404 (master not yet built — Pack P).
@router.get("/recordings/{recording_id}/media/{media_file_id}/raw", summary="Download media file content")
async def download_media_file_raw(
    recording_id: int, media_file_id: int,
    request: Request,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth

    # Resolve the storage path and content type
    storage_path = None
    ct = "application/octet-stream"
    filename = ""

    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    for f in rec.get("media_files") or []:
        if int(f.get("id", -1)) == media_file_id:
            storage_path = f.get("storage_path")
            fmt = str(f.get("format", "bin")).lower()
            type_label = str(f.get("type", "audio"))
            ct = media_content_type(type_label, fmt)
            filename = f"{recording_id}_{type_label}.{fmt}"
            break

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
