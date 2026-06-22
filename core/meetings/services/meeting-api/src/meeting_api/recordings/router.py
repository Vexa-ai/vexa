"""The recordings routes — mounted onto the unified meeting-api app (modular monolith, P2).

  * **POST /internal/recordings/upload** — the bot's chunk upload. Auth via the MeetingToken it
    carries (``Authorization: Bearer <token>``, re-verified here — the parent's
    ``require_recording_upload_token``). Multipart form: ``file`` + ``session_uid`` + media metadata.
    Folds the chunk into ``meeting.data['recordings']`` JSONB. ``include_in_schema=False`` (internal).
  * **GET /recordings** — the caller's recordings (from ``meeting.data``), scoped by the
    gateway-injected ``x-user-id``.
  * **GET /recordings/{recording_id}/master?type=audio|video** — finalize-on-read: build + upload the
    master if absent, then return its storage key. (The byte stream / Range download is P3.)
"""
from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from .ports import RecordingRepo, Storage
from .service import SessionNotFound, _verify_meeting_token, finalize_master, upload_chunk


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing recording upload token")
    return authorization.split(" ", 1)[1].strip()


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def build_router(
    repo: RecordingRepo,
    storage: Storage,
    *,
    token_secret: Optional[str] = None,
) -> APIRouter:
    """The recordings routes over the injected ``RecordingRepo`` + ``Storage`` ports."""
    router = APIRouter()

    @router.post("/internal/recordings/upload", include_in_schema=False)
    async def internal_upload_recording(
        file: UploadFile = File(...),
        session_uid: Optional[str] = Form(None),
        media_type: Optional[str] = Form(None),
        media_format: Optional[str] = Form(None),
        chunk_seq: Optional[int] = Form(None),
        is_final: Optional[bool] = Form(None),
        duration_seconds: Optional[float] = Form(None),
        sample_rate: Optional[int] = Form(None),
        metadata: Optional[str] = Form(None),
        authorization: Optional[str] = Header(default=None),
    ):
        # The bot's RecordingService sends a JSON `metadata` part + the `file` (with flat chunk_seq/
        # is_final on chunk uploads). Parse `metadata` for the fields it carries (session_uid lives
        # only there); any flat Form field overrides its metadata counterpart.
        meta: dict = {}
        if metadata:
            try:
                meta = json.loads(metadata)
            except (ValueError, TypeError):
                meta = {}
        session_uid = session_uid or meta.get("session_uid")
        if not session_uid:
            raise HTTPException(status_code=422, detail="session_uid required (flat field or metadata)")
        media_type = media_type or meta.get("media_type") or "audio"
        media_format = media_format or meta.get("media_format") or meta.get("format") or "wav"
        chunk_seq = chunk_seq if chunk_seq is not None else int(meta.get("chunk_seq", 0) or 0)
        is_final = is_final if is_final is not None else bool(meta.get("is_final", True))
        duration_seconds = duration_seconds if duration_seconds is not None else meta.get("duration_seconds")
        sample_rate = sample_rate if sample_rate is not None else meta.get("sample_rate")

        # Auth: accept either the INTERNAL_API_SECRET (the bot's internal upload uses it, like the
        # lifecycle callback; meeting is scoped by session_uid) OR a MeetingToken (carries its meeting_id).
        bearer = _bearer_token(authorization)
        internal_secret = os.getenv("INTERNAL_API_SECRET")
        token_meeting_id: Optional[int] = None
        if internal_secret and bearer == internal_secret:
            token_meeting_id = None  # internal auth → scope by session; skip the MeetingToken cross-check
        else:
            try:
                claims = _verify_meeting_token(bearer, secret=token_secret)
            except ValueError as e:
                raise HTTPException(status_code=401, detail=f"Invalid recording upload token: {e}")
            token_meeting_id = int(claims["meeting_id"])

        data = await file.read()
        try:
            receipt = await upload_chunk(
                repo, storage,
                token_meeting_id=token_meeting_id,
                session_uid=session_uid, data=data,
                media_type=media_type, media_format=media_format,
                chunk_seq=chunk_seq, is_final=is_final,
                duration_seconds=duration_seconds, sample_rate=sample_rate,
            )
        except SessionNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        return JSONResponse(content=receipt)

    @router.get("/recordings")
    async def list_recordings(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        recs = await repo.list_meeting_recordings(user_id)
        return JSONResponse(content={"recordings": recs})

    @router.get("/recordings/{recording_id}/master")
    async def get_recording_master(
        recording_id: int,
        request: Request,
        type: str = "audio",
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        # Find which meeting owns this recording (scoped to the caller).
        recs = await repo.list_meeting_recordings(user_id)
        rec = next((r for r in recs if r.get("id") == recording_id), None)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        mf = next((m for m in rec.get("media_files", []) if m.get("type") == type), None)
        master_key = await finalize_master(
            repo, storage, meeting_id=rec["meeting_id"], recording_id=recording_id, media_type=type
        )
        if master_key is None:
            raise HTTPException(status_code=404, detail="No such media file to finalize")
        # The dashboard player (api.ts getRecordingMasterStreamUrl) reads ``raw_url`` and streams it via
        # the proxy — the master metadata (``storage_path``) alone is not playable. Point it at the byte
        # route below so playback actually resolves (recordings P3: master byte stream).
        media_file_id = (mf or {}).get("id")
        raw_url = (
            f"/recordings/{recording_id}/media/{media_file_id}/raw?type={type}"
            if media_file_id is not None
            else None
        )
        return JSONResponse(content={
            "id": recording_id,
            "type": type,
            "storage_path": master_key,
            "media_file_id": media_file_id,
            "raw_url": raw_url,
            "duration_seconds": (mf or {}).get("duration_seconds"),
        })

    @router.get("/recordings/{recording_id}/media/{media_file_id}/raw")
    async def get_recording_media_raw(
        recording_id: int,
        media_file_id: int,
        request: Request,
        type: str = "audio",
        x_user_id: Optional[str] = Header(default=None),
    ):
        # Stream the finalized master bytes from object storage (recordings P3). The player fetches
        # /master first (which finalizes), then this; finalize-on-read here too as a safety net.
        user_id = _resolve_user_id(x_user_id)
        recs = await repo.list_meeting_recordings(user_id)
        rec = next((r for r in recs if r.get("id") == recording_id), None)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        mf = next(
            (m for m in rec.get("media_files", []) if str(m.get("id")) == str(media_file_id)),
            None,
        )
        if mf is None:
            raise HTTPException(status_code=404, detail="No such media file")
        if not mf.get("is_final"):
            await finalize_master(
                repo, storage, meeting_id=rec["meeting_id"], recording_id=recording_id,
                media_type=mf.get("type", type),
            )
            recs = await repo.list_meeting_recordings(user_id)
            rec = next((r for r in recs if r.get("id") == recording_id), rec)
            mf = next(
                (m for m in (rec or {}).get("media_files", []) if str(m.get("id")) == str(media_file_id)),
                mf,
            )
        storage_path = mf.get("storage_path")
        if not storage_path:
            raise HTTPException(status_code=404, detail="Media file has no storage path")
        data = await storage.get(storage_path)
        media_format = mf.get("format", "webm")
        if media_format == "wav":
            content_type = "audio/wav"
        elif media_format == "webm":
            content_type = "audio/webm" if mf.get("type") == "audio" else "video/webm"
        else:
            content_type = "application/octet-stream"
        return Response(
            content=data,
            media_type=content_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(len(data))},
        )

    return router
