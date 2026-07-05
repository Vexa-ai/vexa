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

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .obs import TraceMiddleware as _DefaultTraceMiddleware
from .obs import log_event as _default_log_event
from .ports import RedisBus, TranscriptStore


# The two INTENT states the USER owns (pre-FSM). The user dropdown is the source of truth for
# these; they sit BEFORE `requested` and are NEVER passed to the bot FSM (LifecycleSink.apply_change).
_INTENT_STATUSES = frozenset({"idle", "scheduled"})
# FSM-owned values the intent endpoint MUST reject (422) — the bot lifecycle owns everything from
# `requested` onward (machine.py); the user cannot set these directly.
_FSM_OWNED_STATUSES = frozenset({
    "requested", "joining", "awaiting_admission", "needs_help",
    "active", "stopping", "completed", "failed",
})


async def _publish_user_meeting_status(
    redis,
    *,
    user_id,
    meeting_id,
    native_id,
    status: str,
    when: Optional[str],
    log_event: Callable[..., dict],
) -> None:
    """Best-effort publish of a FLAT ``meeting.status`` frame to the user-scoped channel
    ``u:{user_id}:meetings`` so the terminal's list surface gets every status change over WS
    (the gateway forwards the redis payload verbatim). No-op if redis is down / args missing."""
    if redis is None or user_id is None or meeting_id is None:
        return
    import json as _json

    frame = {
        "type": "meeting.status",
        "meeting_id": meeting_id,
        "native": native_id,
        "status": status,
        "when": when,
    }
    try:
        await redis.publish(f"u:{user_id}:meetings", _json.dumps(frame))
    except Exception as e:  # noqa: BLE001 — publish is best-effort
        log_event("user_meeting_status_publish_failed", audience="system", level="warning",
                  span="meetings.intent.publish", fields={"error": str(e)})


def _resolve_user_id(x_user_id: Optional[str]) -> int:
    """The gateway injects ``x-user-id`` after it resolves ``x-api-key`` (anti-spoofing: it
    strips any client-supplied identity header first). Missing → 401 fail-closed."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing user identity")
    try:
        return int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def build_router(
    store: TranscriptStore,
    redis: RedisBus,
    *,
    log_event: Callable[..., dict] = _default_log_event,
) -> APIRouter:
    """The collector's READ-side + authorizer routes as a mountable ``APIRouter``.

    The same handlers ``create_app`` registers, factored out so the unified meeting-api app
    (``meeting_api.app.create_app``) can ``include_router`` them onto its ONE FastAPI app
    alongside lifecycle / bot_spawn / recordings — the modular-monolith composition (P2). The
    standalone ``create_app`` below mounts this same router under its own ``/health`` +
    TraceMiddleware so the conformance harness + this module's tests keep driving shipped code.
    """
    router = APIRouter()

    # --- GET /transcripts/by-id/{meeting_id} → api.v1 TranscriptionResponse for an EXACT row (P0).
    # Registered BEFORE /transcripts/{platform}/{native_meeting_id} so `by-id` is not swallowed as a
    # platform. Owner-scoped: the row must belong to the caller (X-User-Id) or 404 — so it can neither
    # leak another tenant's transcript NOR (unlike the native path, which resolves to the NEWEST row)
    # hydrate the wrong one of a user's several rows on the same meeting link. The terminal fetches the
    # EXACT row it is displaying by its id. ---
    @router.get("/transcripts/by-id/{meeting_id}")
    async def get_transcript_by_id(
        meeting_id: int,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        member_workspaces = {w.strip() for w in (x_user_workspaces or "").split(",") if w.strip()}
        doc = await store.get_transcript_by_id(user_id, meeting_id, member_workspaces)
        if doc is None:
            log_event(
                "transcript_not_found", audience="system", level="warning",
                span="transcripts.get_by_id", user_id=user_id, meeting_id=str(meeting_id),
            )
            raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")
        log_event(
            "transcript_served", audience="user", span="transcripts.get_by_id",
            user_id=user_id, meeting_id=str(meeting_id),
            fields={"segments": len(doc.get("segments", []))},
        )
        return JSONResponse(content=doc)

    # --- GET /transcripts/{platform}/{native_meeting_id} → api.v1 TranscriptionResponse ---
    @router.get("/transcripts/{platform}/{native_meeting_id}")
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
    @router.get("/meetings")
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

    # --- GET /bots → the dashboard's primary meetings-list source (api.v1). Same DB query + shape as
    # GET /meetings, plus `has_more` for the proxy's pagination. ---
    @router.get("/bots")
    async def list_bots(
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
            "bots_listed", audience="user", span="bots.list",
            user_id=user_id, fields={"count": len(meetings)},
        )
        return JSONResponse(content={"meetings": meetings, "has_more": False})

    # --- GET /bots/status → the caller's currently-running bots (api/meetings.mdx "Running bots").
    # Running == any non-terminal FSM status (requested·joining·awaiting_admission·active·stopping);
    # terminal (completed·failed) rows are excluded. Owner-scoped via X-User-Id. ---
    _RUNNING_STATUSES = ("requested", "joining", "awaiting_admission", "active", "stopping")

    @router.get("/bots/status")
    async def bots_status(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        platform: Optional[str] = Query(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meetings = await store.list_meetings(user_id, platform=platform)
        running = [m for m in meetings if m.get("status") in _RUNNING_STATUSES]
        log_event(
            "bots_status", audience="user", span="bots.status",
            user_id=user_id, fields={"running": len(running)},
        )
        return JSONResponse(content={"running": running, "count": len(running)})

    # --- GET /meetings/{meeting_id} → the single meeting (api.v1; the meeting-detail page fetches it).
    # Reuses list_meetings + filters by id (owner-scoped, so a non-owner can't read another's meeting). ---
    @router.get("/meetings/{meeting_id}")
    async def get_meeting(
        request: Request,
        meeting_id: int,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meetings = await store.list_meetings(user_id)
        meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
        if meeting is None:
            return JSONResponse(status_code=404, content={"detail": "Meeting not found"})
        return JSONResponse(content=meeting)

    # --- POST /meetings/{platform}/{native_meeting_id}/workspace → BIND the meeting to a shared workspace
    # (meetings.data.workspace_id). Owner-scoped. Members of that workspace can then subscribe to this
    # meeting's live transcript feed (authorize_subscribe branch b). Many meetings → one workspace. ---
    @router.post("/meetings/{platform}/{native_meeting_id}/workspace")
    async def bind_workspace(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        workspace_id = str(payload.get("workspace_id", "")).strip() if isinstance(payload, dict) else ""
        if not workspace_id:
            raise HTTPException(status_code=422, detail="'workspace_id' is required")
        bound = await store.bind_workspace(user_id, platform, native_meeting_id, workspace_id)
        if bound is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        log_event(
            "meeting_workspace_bound", audience="user", span="meetings.workspace.bind",
            user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
            fields={"workspace_id": workspace_id},
        )
        return JSONResponse(content={"workspace_id": bound})

    # --- POST /meetings/{platform}/{native_meeting_id}/share → mint an INDEPENDENT transcript share link
    # (capability token). Owner-scoped. open|restricted(+allowed_emails), TTL. The token is returned ONCE
    # (only its hash is stored). Redeemed at POST /transcripts/share/accept — NO workspace involved. ---
    @router.post("/meetings/{platform}/{native_meeting_id}/share")
    async def mint_transcript_share(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        mode = str(payload.get("mode", "open")).strip() or "open"
        emails = payload.get("allowed_emails") or []
        ttl = int(payload.get("expires_in_sec", 86400) or 86400)
        minted = await store.mint_transcript_share(
            user_id, platform, native_meeting_id, mode=mode, allowed_emails=emails, expires_in_sec=ttl,
        )
        if minted is None:
            raise HTTPException(status_code=404, detail=f"Meeting not found for {platform}/{native_meeting_id}")
        log_event("transcript_share_minted", audience="user", span="meetings.transcript.share",
                  user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}", fields={"mode": mode})
        return JSONResponse(content=minted)

    # --- POST /transcripts/share/accept → redeem a transcript share token (any authenticated user) →
    # subscribe access to that meeting's live feed. Token carries the meeting; NO workspace. ---
    @router.post("/transcripts/share/accept")
    async def accept_transcript_share(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_email: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        token = str(payload.get("token", "")).strip() if isinstance(payload, dict) else ""
        if not token:
            raise HTTPException(status_code=422, detail="'token' is required")
        result = await store.redeem_transcript_share(user_id, x_user_email, token)
        if result is None:
            raise HTTPException(status_code=404, detail="invalid or unknown share token")
        if result.get("error"):
            code = 403 if result["error"] in ("not_allowed", "revoked", "expired") else 400
            raise HTTPException(status_code=code, detail=result["error"])
        log_event("transcript_share_accepted", audience="user", span="meetings.transcript.accept",
                  user_id=user_id, meeting_id=str(result.get("meeting_id")), fields={})
        return JSONResponse(content=result)

    # --- POST /meetings/{platform}/{native_meeting_id}/docs → connect a workspace doc to a meeting.
    # Appends {workspace, path, title?, kind?} to meeting.data['docs'], deduped by path (idempotent).
    # Owner-scoped. Returns the updated docs array. Doc bodies live in the agent workspace — only the
    # ref lands here. ---
    @router.post("/meetings/{platform}/{native_meeting_id}/docs")
    async def connect_doc(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="body must be an object")
        path = str(payload.get("path", "")).strip()
        workspace = str(payload.get("workspace", "")).strip()
        if not path:
            raise HTTPException(status_code=422, detail="'path' is required")
        if not workspace:
            raise HTTPException(status_code=422, detail="'workspace' is required")
        doc = {"workspace": workspace, "path": path}
        for k in ("title", "kind"):
            if payload.get(k) is not None:
                doc[k] = payload[k]
        docs = await store.connect_doc(user_id, platform, native_meeting_id, doc)
        if docs is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        log_event(
            "meeting_doc_connected", audience="user", span="meetings.docs.connect",
            user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
            fields={"path": path, "docs": len(docs)},
        )
        return JSONResponse(content={"docs": docs})

    # --- DELETE /meetings/{platform}/{native_meeting_id}/docs → disconnect a doc by path (body or
    # query ?path=). Owner-scoped, idempotent. Returns the updated docs array. ---
    @router.delete("/meetings/{platform}/{native_meeting_id}/docs")
    async def disconnect_doc(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        path: Optional[str] = Query(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        resolved = (path or "").strip()
        if not resolved:
            try:
                payload = await request.json()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                resolved = str(payload.get("path", "")).strip()
        if not resolved:
            raise HTTPException(status_code=422, detail="'path' is required")
        docs = await store.disconnect_doc(user_id, platform, native_meeting_id, resolved)
        if docs is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        log_event(
            "meeting_doc_disconnected", audience="user", span="meetings.docs.disconnect",
            user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
            fields={"path": resolved, "docs": len(docs)},
        )
        return JSONResponse(content={"docs": docs})

    # --- PUT /meetings/{platform}/{native_meeting_id}/intent → set the USER-owned INTENT status.
    # The user dropdown is the source of truth for the pre-FSM states `idle` / `scheduled`. Writes
    # meetings.status to `idle`|`scheduled` ONLY; rejects (422) any FSM-owned value. For `scheduled`
    # with `at`, the ISO8601 time is stamped into meeting.data['scheduled_at'] (scheduler wiring is a
    # later track). Owner-scoped. On a genuine change, publishes the flat frame to u:{user_id}:meetings.
    @router.put("/meetings/{platform}/{native_meeting_id}/intent")
    async def set_intent(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="body must be an object")
        intent = payload.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            raise HTTPException(status_code=422, detail="'intent' is required")
        intent = intent.strip()
        if intent in _FSM_OWNED_STATUSES:
            raise HTTPException(
                status_code=422,
                detail=f"'{intent}' is FSM-owned and cannot be set as an intent",
            )
        if intent not in _INTENT_STATUSES:
            raise HTTPException(
                status_code=422,
                detail="'intent' must be one of: idle, scheduled",
            )
        scheduled_at = payload.get("at")
        if scheduled_at is not None and not isinstance(scheduled_at, str):
            raise HTTPException(status_code=422, detail="'at' must be an ISO8601 string")
        if intent == "scheduled" and not scheduled_at:
            raise HTTPException(status_code=422, detail="'at' is required when intent is 'scheduled'")

        result = await store.set_intent(
            user_id, platform, native_meeting_id, intent, scheduled_at=scheduled_at
        )
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        log_event(
            "meeting_intent_set", audience="user", span="meetings.intent.set",
            user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
            fields={"intent": intent, "scheduled_at": result.get("scheduled_at"),
                    "changed": result.get("changed")},
        )
        # Echo over WS — but ONLY on a genuine change (idempotent PUT to the current state does NOT
        # re-publish, mirroring the FSM's no_op discipline so reconnect storms don't fan out).
        if result.get("changed"):
            await _publish_user_meeting_status(
                redis,
                user_id=user_id,
                meeting_id=result.get("id"),
                native_id=native_meeting_id,
                status=intent,
                when=result.get("scheduled_at"),
                log_event=log_event,
            )
        return JSONResponse(content={
            "meeting_id": result.get("id"),
            "status": intent,
            "scheduled_at": result.get("scheduled_at"),
        })

    # --- POST /ws/authorize-subscribe → the gateway /ws authorizer hop ---
    @router.post("/ws/authorize-subscribe")
    async def ws_authorize_subscribe(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        # Lane A: the gateway-injected set of shared workspaces the caller is a member of — authorizes a
        # subscribe to any meeting BOUND to one of them (not just meetings they own). Comma-separated ids.
        member_workspaces = {w.strip() for w in (x_user_workspaces or "").split(",") if w.strip()}
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
            meeting_id = await store.authorize_subscribe(user_id, platform_value, native_id, member_workspaces)
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

    return router


def create_app(
    store: TranscriptStore,
    redis: RedisBus,
    *,
    log_event: Callable[..., dict] = _default_log_event,
    trace_middleware: type = _DefaultTraceMiddleware,
) -> FastAPI:
    """Build the STANDALONE collector FastAPI app over the injected ports.

    Used by the gateway conformance harness + this module's own tests (it is no longer a
    separately-deployed service — the unified ``meeting_api.app.create_app`` mounts
    ``build_router`` instead, and exposes the one shared ``/health``). Keeping ``create_app``
    means those harnesses keep driving the SAME shipped handlers.

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

    app.include_router(build_router(store, redis, log_event=log_event))
    return app
