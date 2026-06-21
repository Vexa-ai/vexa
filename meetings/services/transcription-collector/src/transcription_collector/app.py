"""``create_app(store, redis, ...) -> FastAPI`` — the PRODUCTION transcription-collector.

This is the single source of the transcript backend the gateway proxies to. Its behavior is the
v0.12 carve of the deployed ``services/meeting-api/meeting_api/collector/endpoints.py``:

  * **GET /transcripts/{platform}/{native_meeting_id}** — the meeting's transcript document,
    conforming to api.v1 ``#/components/schemas/TranscriptionResponse`` (sealed). 404 when the
    caller owns no such meeting.
  * **GET /meetings** — the caller's meetings, conforming to api.v1
    ``#/components/schemas/MeetingListResponse`` (sealed). Optional ``status`` / ``platform`` /
    ``limit`` / ``offset`` filters (parent's ``get_meetings``).
  * **POST /ws/authorize-subscribe** — the gateway's ``/ws`` subscribe-authorization hop: given
    ``{meetings:[{platform, native_meeting_id}]}`` + the identity headers the gateway injects,
    returns ``{authorized:[{platform, native_id, user_id, meeting_id}], errors:[]}`` — the exact
    shape ``gateway.ports.Authorizer.authorize_subscribe`` consumes (``gateway`` adapters POST
    here, ``_run_multiplex`` reads ``authorized[].{platform,native_id,user_id,meeting_id}``).
  * **/health** — liveness ``{status:"ok", service:"transcription-collector"}`` (gate:health).

The caller's identity arrives in the ``x-user-id`` header the gateway injects after it resolves
``x-api-key`` (``gateway.app._forward`` / ``AdminApiAuthorizer.authorize_subscribe``) — the
collector trusts it (it sits behind the gateway), exactly as the parent's ``UserProxy`` does.

Collaborators (store, redis) are injected as PORTS (``ports.py``) so the same app runs with real
adapters in prod (``adapters.py``) and in-process fakes in the conformance harness — the
conformance assertions therefore drive SHIPPED code.

The edge threads ``logevent.v1`` trace_id: ``TraceMiddleware`` reads the gateway-forwarded
``X-Trace-Id`` and binds it so this hop's logs join the same trace. The middleware + emitter are
injectable so the in-process conformance chain can bind a collector-emitter that shares the
gateway's contextvars (the cross-hop trace ``test_tracing.py`` asserts).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .obs import TraceMiddleware as _DefaultTraceMiddleware
from .obs import log_event as _default_log_event
from .ports import RedisBus, TranscriptStore


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    """The gateway injects ``x-user-id`` after it resolves ``x-api-key`` (anti-spoofing: it
    strips any client-supplied identity header first). Missing → 401 fail-closed."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def create_app(
    store: TranscriptStore,
    redis: RedisBus,
    *,
    log_event: Callable[..., dict] = _default_log_event,
    trace_middleware: type = _DefaultTraceMiddleware,
) -> FastAPI:
    """Build the collector FastAPI app over the injected ports.

    ``store`` — read transcripts / list meetings / authorize subscribe / append segments.
    ``redis`` — the segment-ingestion bus (consumed by ``ingest`` / ``consume_segments``).
    ``log_event`` / ``trace_middleware`` — the lane's logevent.v1 emitter (injectable so the
    in-process conformance chain binds the gateway's shared contextvars).
    """
    app = FastAPI(title="Vexa Transcription Collector (v0.12)")
    # The hop: read the gateway-forwarded X-Trace-Id and bind it (logevent.v1 trace_id).
    app.add_middleware(trace_middleware)

    # --- liveness probe (gate:health): the collector process is up. No auth, no store call. ---
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "transcription-collector"}

    # --- GET /transcripts/{platform}/{native_meeting_id} → api.v1 TranscriptionResponse ---
    @app.get("/transcripts/{platform}/{native_meeting_id}")
    async def get_transcript(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        doc = await store.get_transcript(user_id, platform, native_meeting_id)
        if doc is None:
            log_event(
                "transcript_not_found",
                audience="system",
                level="warning",
                span="transcripts.get",
                user_id=user_id,
                meeting_id=f"{platform}/{native_meeting_id}",
            )
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        # USER-facing: this user read their transcript.
        log_event(
            "transcript_served",
            audience="user",
            span="transcripts.get",
            user_id=user_id,
            meeting_id=f"{platform}/{native_meeting_id}",
            fields={"segments": len(doc.get("segments", []))},
        )
        return JSONResponse(content=doc)

    # --- GET /meetings → api.v1 MeetingListResponse ---
    @app.get("/meetings")
    async def get_meetings(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        limit: Optional[int] = Query(default=None, ge=1, le=100),
        offset: Optional[int] = Query(default=None, ge=0),
        status: Optional[str] = Query(default=None),
        platform: Optional[str] = Query(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meetings = await store.list_meetings(
            user_id, status=status, platform=platform, limit=limit, offset=offset
        )
        log_event(
            "meetings_listed",
            audience="user",
            span="meetings.list",
            user_id=user_id,
            fields={"count": len(meetings)},
        )
        return JSONResponse(content={"meetings": meetings})

    # --- POST /ws/authorize-subscribe → the gateway /ws authorizer hop ---
    @app.post("/ws/authorize-subscribe")
    async def ws_authorize_subscribe(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        meetings = payload.get("meetings") if isinstance(payload, dict) else None
        if not isinstance(meetings, list) or not meetings:
            raise HTTPException(status_code=422, detail="'meetings' must be a non-empty list")

        authorized: list[dict[str, Any]] = []
        errors: list[str] = []
        for idx, ref in enumerate(meetings):
            if not isinstance(ref, dict):
                errors.append(f"meetings[{idx}] must be an object")
                continue
            platform_value = str(ref.get("platform", "")).strip()
            native_id = str(ref.get("native_meeting_id", "")).strip()
            # URL-constructibility is advisory only — the DB ownership check below is the actual
            # authorization boundary (parent ws_authorize_subscribe). Bound the id length.
            if not native_id or len(native_id) > 255:
                errors.append(
                    f"meetings[{idx}] invalid native_meeting_id for platform '{platform_value}'"
                )
                continue
            meeting_id = await store.authorize_subscribe(user_id, platform_value, native_id)
            if meeting_id is None:
                errors.append(f"meetings[{idx}] not authorized or not found for user")
                continue
            authorized.append({
                "platform": platform_value,
                "native_id": native_id,
                "user_id": str(user_id),
                "meeting_id": str(meeting_id),
            })

        log_event(
            "ws_subscribe_authorized",
            audience="system",
            span="ws.authorize_subscribe",
            user_id=user_id,
            fields={"authorized": len(authorized), "errors": len(errors)},
        )
        return JSONResponse(content={"authorized": authorized, "errors": errors, "user_id": user_id})

    return app
