"""The two meeting-bundle routes.

* ``GET  /meetings/{meeting_id}/export[?media=false]`` — the meeting as a meeting-bundle.v1 zip,
  for its owner (``tx``).
* ``POST /meetings/import[?dry_run=true]`` — the zip as the request body (``application/zip``); with
  ``dry_run`` it answers what the import WOULD create and writes nothing; without it, it creates a
  new meeting owned by the caller (``tx``).

Every refusal answers ``{"detail": {"code", "detail"}}`` with one contract code, so a client tells a
bad hash from a duplicate from a too-big archive without parsing prose.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from .codec import MAX_BUNDLE_BYTES, BundleRefused
from .service import ExportError, export_meeting, import_bundle

# The status each refusal answers with: the archive's SIZE is 413, a meeting that already exists is
# 409, every other refusal is about the archive's content (422).
_STATUS = {"too_large": 413, "duplicate_import": 409}


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


async def _read_capped(request: Request) -> bytes:
    """The request body, refused the moment it passes the cap — the declared length first, then
    the bytes actually received, so a client that lies about Content-Length is cut off too."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BUNDLE_BYTES:
        raise BundleRefused("too_large", f"the bundle is {declared} bytes (max {MAX_BUNDLE_BYTES})")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BUNDLE_BYTES:
            raise BundleRefused("too_large", f"the bundle exceeds {MAX_BUNDLE_BYTES} bytes")
    return bytes(body)


def build_router(
    store, recording_repo, storage, *,
    finalize: Callable, log_event: Callable, secret: Optional[str] = None,
) -> APIRouter:
    router = APIRouter()

    # Registered before any `/meetings/{meeting_id}` POST could exist; a literal `import` segment is
    # never an integer row id, so neither route can answer for the other.
    @router.post("/meetings/import")
    async def import_meeting(
        request: Request,
        dry_run: bool = Query(default=False, description="Answer what would be imported; write nothing."),
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            raw = await _read_capped(request)
            result = await import_bundle(store, recording_repo, storage, user_id=user_id, raw=raw,
                                         dry_run=dry_run, log_event=log_event)
        except BundleRefused as refused:
            log_event("meeting_bundle_refused", audience="user", level="warning",
                      span="meetings.bundle.import", user_id=user_id,
                      fields={"code": refused.code, "detail": refused.detail, "dry_run": dry_run})
            raise HTTPException(status_code=_STATUS.get(refused.code, 422), detail=refused.body())
        return JSONResponse(status_code=200 if dry_run else 201, content=result)

    @router.get("/meetings/{meeting_id}/export")
    async def export_meeting_bundle(
        meeting_id: int,
        media: bool = Query(default=True, description="Include recordings (false: transcript and metadata only)."),
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        # The caller's workspaces are read for the same reason the detail view reads them: a member
        # who can SEE the meeting is told it is not theirs to export (403), not that it is missing.
        member_workspaces = {w.strip() for w in (x_user_workspaces or "").split(",") if w.strip()}
        try:
            archive, filename = await export_meeting(
                store, recording_repo, storage, user_id=user_id, meeting_id=meeting_id,
                member_workspaces=member_workspaces,
                secret=secret, include_media=media, finalize=finalize)
        except ExportError as e:
            log_event("meeting_bundle_export_failed", audience="user", level="warning",
                      span="meetings.bundle.export", user_id=user_id, meeting_id=str(meeting_id),
                      fields={"status": e.status, "detail": e.detail})
            raise HTTPException(status_code=e.status, detail=e.detail)
        log_event("meeting_bundle_exported", audience="user", span="meetings.bundle.export",
                  user_id=user_id, meeting_id=str(meeting_id),
                  fields={"bytes": len(archive), "media": media})
        return Response(content=archive, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(archive)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        })

    return router
