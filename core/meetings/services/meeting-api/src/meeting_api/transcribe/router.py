"""POST /meetings/{meeting_id}/transcribe, the sealed api.v1 route (#525 C1).

The contract stops lying: the route either serves a transcript from a completed
meeting's recording or refuses with a typed reason. Fault→HTTP mapping lives
here; the flow itself is ``service.transcribe_meeting``.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Header, HTTPException

from ..obs import log_event
from .service import TranscribeFault, transcribe_meeting

_FAULT_STATUS = {
    "not_found": 404,
    "not_completed": 409,         # still recording, a partial transcript would 409-block the full one
    "no_recording": 404,          # prepared: a reasoned 404, never a 500
    "already_transcribed": 409,   # Q2 ruling: refuse a second run, typed
    "already_running": 409,       # concurrent duplicate, the first run is still transcribing
    "no_segments": 502,
    "provider_rejected": 502,
    "unavailable": 503,
    "stt_unconfigured": 503,
}


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def build_router(store, stt, resolve_master) -> APIRouter:
    router = APIRouter()

    @router.post("/meetings/{meeting_id}/transcribe")
    async def transcribe(
        meeting_id: int,
        body: Optional[dict] = Body(default=None),
        x_user_id: Optional[str] = Header(default=None),
    ):
        """api/meetings.mdx: transcribe a completed meeting recording. Optional body
        ``{"language": "en"}`` forwards a language hint to the STT backend."""
        user_id = _resolve_user_id(x_user_id)
        language = (body or {}).get("language")
        try:
            result = await transcribe_meeting(
                store=store, stt=stt, resolve_master=resolve_master,
                user_id=user_id, meeting_id=meeting_id, language=language,
            )
        except TranscribeFault as fault:
            log_event(
                "deferred_transcribe_refused", audience="user", level="warning",
                span="transcribe", user_id=user_id, meeting_id=str(meeting_id),
                fields={"reason": fault.kind, "provider_code": fault.provider_code},
            )
            detail = {"reason": fault.kind, "message": fault.detail}
            if fault.provider_code:
                detail["provider_code"] = fault.provider_code
            raise HTTPException(
                status_code=_FAULT_STATUS.get(fault.kind, 502), detail=detail,
            )
        log_event(
            "deferred_transcribed", audience="user", span="transcribe",
            user_id=user_id, meeting_id=str(meeting_id),
            fields={"segments_stored": result["segments_stored"], "language": result["language"]},
        )
        return result

    return router
