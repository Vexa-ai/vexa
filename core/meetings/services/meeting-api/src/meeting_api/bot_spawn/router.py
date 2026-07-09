"""The ``POST /bots`` route — mounts the bot-spawn flow onto the unified meeting-api app.

A mountable ``APIRouter`` (the modular-monolith composition, P2). The caller's identity arrives in
the ``x-user-id`` header the gateway injects after it resolves ``x-api-key`` (the gateway strips any
client-supplied identity header first — anti-spoofing). The route maps the spawn outcomes onto the
HTTP status the gateway forwards verbatim:

  * 201 + ``api.v1`` MeetingResponse on success,
  * 409 when the user already has an active meeting for (platform, native_id),
  * 429 when the runtime kernel rejects the spawn for owner quota,
  * 502 when the kernel could not start the workload.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config_preflight import CONFIGURED, capability_state, missing_capability_keys
from .ports import MaxBotsExceeded, MeetingRepo, QuotaExceeded, RuntimeClient, SpawnFailed
from .service import DuplicateMeeting, construct_meeting_url, request_bot


def _resolve_recording_enabled(value: Optional[object]) -> bool:
    """Recording default: an explicit request value wins; else the ``RECORDING_ENABLED`` env
    (default ``true``), so a dashboard bot records by default. The request value is type-validated —
    a bool is honored, a string is parsed (``"true"``/``"false"`` etc.), and any other type is a 422
    (NOT silently ``bool()``-coerced, which would turn the string ``"false"`` into ``True``)."""
    if value is None:
        return os.getenv("RECORDING_ENABLED", "true").lower() == "true"
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    raise HTTPException(status_code=422, detail="recording_enabled must be a boolean")


def _resolve_transcribe_enabled(value: Optional[object]) -> bool:
    """Transcription default: an explicit request value wins; else the ``TRANSCRIBE_ENABLED`` env
    (default ``true``). Type-validated like ``recording_enabled`` (CC3) — a bare ``bool(...)`` turned the
    JSON string ``"false"`` into ``True``, silently ENABLING transcription a caller asked to disable."""
    if value is None:
        return os.getenv("TRANSCRIBE_ENABLED", "true").lower() == "true"
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    raise HTTPException(status_code=422, detail="transcribe_enabled must be a boolean")


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def _resolve_max_concurrent(x_user_limits: Optional[str]) -> Optional[int]:
    """Parse the gateway's ``X-User-Limits`` header → the per-user max-bots cap (P3e).

    The gateway resolves the user via ``/internal/validate`` (identity.v1) and forwards the limit as
    a header (the parent's ``auth.validate_request`` reads ``X-User-Limits`` as a bare int or a JSON
    ``{"max_concurrent_bots"|"max_concurrent": …}``). Absent/unparseable → ``None`` (no pre-check).
    ``0`` is a REAL value (quota depleted — every spawn rejected), not absence."""
    if not x_user_limits:
        return None
    raw = x_user_limits.strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        pass
    try:
        import json

        obj = json.loads(raw)
        if isinstance(obj, dict):
            v = obj.get("max_concurrent_bots", obj.get("max_concurrent"))
            return int(v) if v is not None else None
    except Exception:
        return None
    return None


def build_router(repo: MeetingRepo, runtime: RuntimeClient) -> APIRouter:
    """The bot-spawn routes over the injected ``MeetingRepo`` + ``RuntimeClient`` ports."""
    router = APIRouter()

    @router.post("/bots", status_code=201)
    async def create_bot(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_limits: Optional[str] = Header(default=None),
        x_user_webhook_url: Optional[str] = Header(default=None),
        x_user_webhook_secret: Optional[str] = Header(default=None),
        x_user_webhook_events: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        max_concurrent = _resolve_max_concurrent(x_user_limits)
        # Per-user webhook config the gateway forwarded from identity (persisted into meeting.data).
        webhook_events = None
        if x_user_webhook_events:
            try:
                import json as _json

                parsed = _json.loads(x_user_webhook_events)
                webhook_events = parsed if isinstance(parsed, dict) else None
            except Exception:
                webhook_events = None
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="body must be an object")

        platform = str(body.get("platform", "")).strip()
        native_meeting_id = str(body.get("native_meeting_id", "")).strip()
        meeting_url = body.get("meeting_url")
        if not platform or (not native_meeting_id and not meeting_url):
            raise HTTPException(
                status_code=422,
                detail="'platform' and 'native_meeting_id' (or 'meeting_url') are required",
            )
        # Reject an unsupported platform up front (→ 422), instead of letting the spawn flow fail deep in
        # the invocation builder with an uncaught jsonschema error (→ 500): a meeting URL must be
        # CONSTRUCTIBLE — the platform has a URL template (google_meet/teams), or the caller supplied an
        # explicit meeting_url (required for zoom + white-label/unknown platforms).
        if not meeting_url and construct_meeting_url(platform, native_meeting_id) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported platform '{platform}' without a meeting_url — "
                    "use google_meet/teams, or provide meeting_url (required for zoom)"
                ),
            )

        # CC4 — fail loud (P18): a transcription bot needs STT. The guard is the CANONICAL config.v1
        # capability gate (ADR-0026): it consults the declared `stt` capability's tri-state — the same
        # declaration that drives the boot preflight and the /health row — instead of ad-hoc os.getenv
        # checks (env-level state, pure: no probe I/O on the spawn path). If transcription resolves ON
        # but stt is not `configured`, refuse the spawn (before any DB write) rather than silently
        # launch a bot that joins + captures but can NEVER transcribe. A no-transcription bot must opt
        # out explicitly: spawn transcribe_enabled=false, or set TRANSCRIBE_ENABLED=false on the deploy.
        transcribe_enabled = _resolve_transcribe_enabled(body.get("transcribe_enabled"))
        stt_state = capability_state("stt")
        if transcribe_enabled and stt_state != CONFIGURED:
            unset = ", ".join(missing_capability_keys("stt"))
            raise HTTPException(
                status_code=503,
                detail=(
                    f"transcription requested but the STT service is not configured "
                    f"(config.v1 capability 'stt' is {stt_state}: {unset} unset) — set them, or spawn "
                    "with transcribe_enabled=false"
                ),
            )

        try:
            meeting = await request_bot(
                repo,
                runtime,
                user_id=user_id,
                platform=platform,
                native_meeting_id=native_meeting_id,
                bot_name=body.get("bot_name"),
                meeting_url=meeting_url,
                language=body.get("language"),
                task=body.get("task"),
                transcription_tier=body.get("transcription_tier", "realtime"),
                recording_enabled=_resolve_recording_enabled(body.get("recording_enabled")),
                transcribe_enabled=transcribe_enabled,
                # P3c — continue_meeting is accepted off the OPEN api.v1 request body (MeetingCreate
                # has no additionalProperties:false), so the wire is not rejected; documenting it as
                # a public typed field needs a vN+1 (lane:contract) — see the bot_spawn README.
                continue_meeting=bool(body.get("continue_meeting", False)),
                max_concurrent=max_concurrent,
                webhook_url=x_user_webhook_url,
                webhook_secret=x_user_webhook_secret,
                webhook_events=webhook_events,
            )
        except DuplicateMeeting as e:
            raise HTTPException(status_code=409, detail=str(e))
        except (MaxBotsExceeded, QuotaExceeded) as e:
            raise HTTPException(status_code=429, detail=str(e) or "Bot concurrency limit reached")
        except SpawnFailed as e:
            raise HTTPException(status_code=502, detail=str(e) or "Failed to start bot workload")

        return JSONResponse(status_code=201, content=meeting)

    return router
