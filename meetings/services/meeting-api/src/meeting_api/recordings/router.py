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

from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

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
        session_uid: str = Form(...),
        media_type: str = Form("audio"),
        media_format: str = Form("wav"),
        chunk_seq: int = Form(0),
        is_final: bool = Form(True),
        duration_seconds: Optional[float] = Form(None),
        sample_rate: Optional[int] = Form(None),
        authorization: Optional[str] = Header(default=None),
    ):
        token = _bearer_token(authorization)
        try:
            claims = _verify_meeting_token(token, secret=token_secret)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=f"Invalid recording upload token: {e}")

        data = await file.read()
        try:
            receipt = await upload_chunk(
                repo, storage,
                token_meeting_id=int(claims["meeting_id"]),
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
        master_key = await finalize_master(
            repo, storage, meeting_id=rec["meeting_id"], recording_id=recording_id, media_type=type
        )
        if master_key is None:
            raise HTTPException(status_code=404, detail="No such media file to finalize")
        return JSONResponse(content={"id": recording_id, "type": type, "storage_path": master_key})

    return router
