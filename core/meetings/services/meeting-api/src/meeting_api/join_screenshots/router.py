"""``POST /internal/join-evidence/screenshot`` — store one join-failure PNG per bot session.

Modelled on the captured-signal tape upload (``recordings.service.upload_signal_tape``): the same
internal edge and credential (the bot's MeetingToken bound to its session, or the internal tier),
the same fail-closed session scoping, and its own keyspace — a screenshot is evidence about a
failed join, not the user's recording, so it never folds into ``meeting.data['recordings']``.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..recordings.ports import RecordingRepo, Storage
from ..recordings.router import _upload_scope

JOIN_SCREENSHOT_PREFIX = "join-evidence/"
#: A full-page Meet/Teams/Zoom screenshot is well under 1 MB; anything past this is not one.
JOIN_SCREENSHOT_MAX_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: The session uid lands in an object key: plain segment characters only (find_session also
#: constrains it to a real session, but the key must be safe before that lookup is trusted).
_SESSION_UID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def screenshot_key(*, meeting_id: int, session_uid: str) -> str:
    """The object key of one attempt's screenshot: one per bot session (= one join attempt)."""
    return f"{JOIN_SCREENSHOT_PREFIX}{int(meeting_id)}/{session_uid}.png"


async def delete_meeting_screenshots(storage: Storage, *, meeting_id: int) -> list[str]:
    """Erase every attempt's screenshot of this meeting (called when the meeting is erased)."""
    prefix = f"{JOIN_SCREENSHOT_PREFIX}{int(meeting_id)}/"
    keys = [key for key in await storage.list(prefix) if key.startswith(prefix)]
    for key in keys:
        await storage.delete(key)
    return keys


def build_router(repo: RecordingRepo, storage: Storage, *, token_secret: Optional[str] = None) -> APIRouter:
    router = APIRouter()

    @router.post("/internal/join-evidence/screenshot", include_in_schema=False)
    async def internal_upload_join_screenshot(
        file: UploadFile = File(...),
        session_uid: str = Form(...),
        authorization: Optional[str] = Header(default=None),
    ):
        if not _SESSION_UID.match(session_uid or ""):
            raise HTTPException(status_code=422, detail="session_uid is not a valid session id")
        # Auth first: an unauthenticated caller learns nothing about sizes or sessions.
        token_meeting_id = _upload_scope(authorization, session_uid, token_secret)
        data = await file.read(JOIN_SCREENSHOT_MAX_BYTES + 1)
        if len(data) > JOIN_SCREENSHOT_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"screenshot larger than {JOIN_SCREENSHOT_MAX_BYTES} bytes")
        # The bytes decide, not the declared type: the key says .png and a reader trusts it.
        if not data.startswith(_PNG_MAGIC):
            raise HTTPException(status_code=415, detail="screenshot must be a PNG")
        session = await repo.find_session(session_uid)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no MeetingSession for session_uid {session_uid}")
        meeting_id = int(session["meeting_id"])
        if token_meeting_id is not None and meeting_id != token_meeting_id:
            # Same rule as a chunk or a tape: a token for another meeting never writes into this one.
            raise HTTPException(status_code=404, detail="MeetingToken meeting_id does not match the session's meeting")
        key = screenshot_key(meeting_id=meeting_id, session_uid=session_uid)
        await storage.upload(key, data, content_type="image/png")
        # A deletion may have started while this last-gasp upload was in flight.
        if await repo.find_session(session_uid) is None:
            await storage.delete(key)
            raise HTTPException(status_code=404, detail="Meeting data was deleted")
        return JSONResponse(content={"status": "stored", "key": key, "bytes": len(data)})

    return router
