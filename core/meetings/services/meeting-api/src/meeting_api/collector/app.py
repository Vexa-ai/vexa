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

import json

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from .meeting_link import parse_meeting_url
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
    calendar_sync_now: Optional[Callable] = None,
    calendar_sync_status: Optional[Callable] = None,
    artifact_object_deleter: Optional[Callable] = None,
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

    # --- GET /transcripts/search?q= → ranked, snippeted hits across the caller's OWN transcripts.
    # Registered BEFORE /transcripts/{platform}/{native_meeting_id} so `search` is not swallowed
    # as a platform — the same ordering `by-id` above depends on.
    #
    # This answers what metadata cannot: not "meetings I tagged X" but "meetings where someone
    # SAID X". Owner-scoped only, deliberately narrower than GET /meetings (which also surfaces
    # share-recipient and workspace rows) — a search that over-returns is a disclosure, so it
    # fails closed until widening it has had its own review.
    @router.get("/transcripts/search")
    async def search_transcripts(
        request: Request,
        q: str = Query(..., min_length=1, description=(
            "Search text. Supports \"quoted phrases\", `or`, and `-excluded` terms "
            "(websearch syntax). Malformed input never errors."
        )),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        platform: Optional[str] = Query(None, description="Restrict to one platform."),
        native_meeting_id: Optional[str] = Query(None, description="Restrict to one meeting."),
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        if not (q or "").strip():
            raise HTTPException(status_code=422, detail="'q' must not be blank")
        hits = await store.search_transcripts(
            user_id, q, limit=limit, offset=offset,
            platform=platform, native_meeting_id=native_meeting_id,
        )
        log_event(
            "transcripts_searched", audience="user", span="transcripts.search",
            user_id=user_id,
            fields={"query_chars": len(q), "hits": len(hits), "platform": platform},
        )
        return JSONResponse(content={"query": q, "hits": hits, "count": len(hits)})

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

    # A planned meeting belongs to schedule/preparation surfaces until a bot run claims it.
    # Keep the set explicit so list pagination can exclude plans in SQL rather than making
    # clients discard rows after a page has already been cut.
    _NON_PLANNED_STATUSES = (
        "requested", "joining", "awaiting_admission", "active", "needs_human_help",
        "stopping", "completed", "failed",
    )

    # --- GET /meetings → api.v1 MeetingListResponse ---
    @router.get("/meetings")
    async def get_meetings(
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
        limit: Optional[int] = Query(default=None, ge=1, le=100),
        offset: Optional[int] = Query(default=None, ge=0),
        status: Optional[str] = Query(default=None),
        platform: Optional[str] = Query(default=None),
        exclude_planned: bool = Query(default=False),
        metadata: Optional[str] = Query(
            default=None,
            description=(
                'JSON object. Returns only meetings whose caller-set metadata CONTAINS it — e.g. '
                '{"crm_deal":"acme-42"}. Containment, so extra keys on the row still match. '
                "Filtered in SQL against the data GIN index, not on the fetched page."
            ),
        ),
    ):
        user_id = _resolve_user_id(x_user_id)
        member_workspaces = {w.strip() for w in (x_user_workspaces or "").split(",") if w.strip()}
        status_filter = status if status is not None else (_NON_PLANNED_STATUSES if exclude_planned else None)
        metadata_filter = None
        if metadata:
            try:
                metadata_filter = json.loads(metadata)
            except Exception:
                raise HTTPException(status_code=422, detail="'metadata' must be a JSON object")
            if not isinstance(metadata_filter, dict):
                raise HTTPException(status_code=422, detail="'metadata' must be a JSON object")
        meetings, _has_more = await store.list_meetings(
            user_id, status=status_filter, platform=platform, limit=limit, offset=offset,
            member_workspaces=member_workspaces, list_view=True, metadata_filter=metadata_filter,
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
        exclude_planned: bool = Query(default=False),
    ):
        user_id = _resolve_user_id(x_user_id)
        status_filter = status if status is not None else (_NON_PLANNED_STATUSES if exclude_planned else None)
        meetings, has_more = await store.list_meetings(
            user_id, status=status_filter, platform=platform, limit=limit, offset=offset,
            list_view=True,
        )
        log_event(
            "bots_listed", audience="user", span="bots.list",
            user_id=user_id, fields={"count": len(meetings)},
        )
        return JSONResponse(content={"meetings": meetings, "has_more": has_more})

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
        # Filtered + projected IN SQL (#803). Reading the caller's whole history and filtering here
        # meant a running-bots badge materialized every meeting they ever had, with full `data` —
        # hundreds of MB for a heavy account, and an OOM under concurrent polls.
        running = await store.list_meetings(
            user_id, status=_RUNNING_STATUSES, platform=platform, slim=True,
        )
        log_event(
            "bots_status", audience="user", span="bots.status",
            user_id=user_id, fields={"running": len(running)},
        )
        # `running_bots` is the sealed api.v1 field (golden BotStatusResponse.example.json) a 0.10
        # client reads for the bot-running badge; `running`/`count` are the 0.12 names. All three are
        # emitted (additive back-compat, #579 C2) — the same list under both keys.
        return JSONResponse(content={
            "running": running, "running_bots": running, "count": len(running),
        })

    # --- GET /meetings/{meeting_id} → the single meeting (api.v1; the meeting-detail page fetches it).
    # Constrained by id IN SQL under the same access union, so a non-owner still cannot read another's
    # meeting — but one row is read instead of the caller's entire history (#803). Full `data` is
    # retained: this IS the detail view. ---
    @router.get("/meetings/{meeting_id}")
    async def get_meeting(
        request: Request,
        meeting_id: int,
        x_user_id: Optional[str] = Header(default=None),
    ):
        from .projection import project_response_data

        user_id = _resolve_user_id(x_user_id)
        meetings = await store.list_meetings(user_id, meeting_id=meeting_id)
        meeting = next((m for m in meetings if m.get("id") == meeting_id), None)
        if meeting is None:
            return JSONResponse(status_code=404, content={"detail": "Meeting not found"})
        # Full `data` minus the raw ICS event snapshot: the projection happens HERE, at the
        # response edge, because the same store call feeds calendar sync — which reads the
        # snapshot and writes it back, so a strip inside the store would erase it.
        #
        # The projection is viewer-aware, and the viewer decision is READ BACK from the row rather
        # than re-derived: `list_meetings` already evaluated its access union and stamped `shared`
        # (`m.user_id != user_id`) on every row it returns. `is False` and not `not ...` on purpose —
        # an absent or unknown `shared` must fall to the STRICT view, not the permissive one.
        viewer_is_owner = meeting.get("shared") is False
        return JSONResponse(content={
            **meeting,
            "data": project_response_data(meeting.get("data"), viewer_is_owner=viewer_is_owner),
        })

    # --- POST /meetings → CREATE a PLANNED meeting (intent status, NO bot spawned). The user plans a
    # meeting ahead of time — with or without a meeting link, with or without a time. Status starts at
    # `scheduled` (time given) or `idle`. A later POST /bots for the same (platform, native) CLAIMS the
    # row in place (bot_spawn.create_meeting_guarded), so the plan, its workspace bind, and the eventual
    # transcript share ONE row. Link-less plans use platform='unknown' + NULL native id and are addressed
    # by ROW id (PATCH/DELETE below). ---
    # ── calendar sync, user-facing: immediate feedback for the "Connect your calendar" panel.
    #    GET  → the last sweep's stamp {last_sync, last_error, counts?} (or {} before any run).
    #    POST → run THIS user's fetch→parse→sync NOW and return the fresh stamp — pasting a feed
    #    answers in seconds ("imported N" / the actual failure), not "wait for the next tick".
    #    Both 503 when the composition root didn't wire the edges (standalone/test app). ──────────
    @router.get("/user/calendar/sync")
    async def calendar_sync_state(x_user_id: Optional[str] = Header(default=None)):
        user_id = _resolve_user_id(x_user_id)
        if calendar_sync_status is None:
            raise HTTPException(status_code=503, detail="calendar sync is not available")
        stamp = await calendar_sync_status(user_id)
        return stamp or {}

    @router.post("/user/calendar/sync")
    async def calendar_sync_run(x_user_id: Optional[str] = Header(default=None)):
        user_id = _resolve_user_id(x_user_id)
        if calendar_sync_now is None:
            raise HTTPException(status_code=503, detail="calendar sync is not available")
        stamp = await calendar_sync_now(user_id)
        if stamp is None:
            raise HTTPException(status_code=404, detail="no calendar feed connected")
        return stamp

    @router.get("/user/calendars/{calendar_id}/sync")
    async def calendar_connection_sync_state(calendar_id: str,
                                             x_user_id: Optional[str] = Header(default=None)):
        user_id = _resolve_user_id(x_user_id)
        if calendar_sync_status is None:
            raise HTTPException(status_code=503, detail="calendar sync is not available")
        stamp = await calendar_sync_status(user_id, calendar_id)
        return stamp or {}

    @router.post("/user/calendars/{calendar_id}/sync")
    async def calendar_connection_sync_run(calendar_id: str,
                                           x_user_id: Optional[str] = Header(default=None)):
        user_id = _resolve_user_id(x_user_id)
        if calendar_sync_now is None:
            raise HTTPException(status_code=503, detail="calendar sync is not available")
        stamp = await calendar_sync_now(user_id, calendar_id)
        if stamp is None:
            raise HTTPException(status_code=404, detail="calendar not found")
        return stamp

    @router.post("/meetings", status_code=201)
    async def create_planned_meeting(
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

        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            raise HTTPException(status_code=422, detail="'title' must be a string")
        title = (title or "").strip()[:512] or None

        scheduled_at = payload.get("scheduled_at")
        if scheduled_at is not None and not isinstance(scheduled_at, str):
            raise HTTPException(status_code=422, detail="'scheduled_at' must be an ISO8601 string")

        meeting_url = payload.get("meeting_url")
        if meeting_url is not None and not isinstance(meeting_url, str):
            raise HTTPException(status_code=422, detail="'meeting_url' must be a string")
        meeting_url = (meeting_url or "").strip() or None
        platform, native_id = "unknown", None
        if meeting_url:
            parsed = parse_meeting_url(meeting_url)
            if parsed is None:
                raise HTTPException(status_code=422, detail="unrecognized 'meeting_url'")
            platform, native_id = parsed

        workspace_id = payload.get("workspace_id")
        if workspace_id is not None and not isinstance(workspace_id, str):
            raise HTTPException(status_code=422, detail="'workspace_id' must be a string")
        workspace_id = (workspace_id or "").strip() or None

        auto_join = payload.get("auto_join", True)
        if not isinstance(auto_join, bool):
            raise HTTPException(status_code=422, detail="'auto_join' must be a boolean")

        row = await store.create_planned_meeting(
            user_id, platform=platform, native_meeting_id=native_id,
            title=title, scheduled_at=scheduled_at, meeting_url=meeting_url,
            workspace_id=workspace_id, auto_join=auto_join,
        )
        if isinstance(row, dict) and row.get("error") == "duplicate":
            raise HTTPException(
                status_code=409,
                detail=f"A meeting already exists for {platform}/{native_id}",
            )
        log_event(
            "meeting_planned", audience="user", span="meetings.plan",
            user_id=user_id, meeting_id=str(row.get("id")),
            fields={"status": row.get("status"), "platform": platform,
                    "scheduled_at": scheduled_at, "has_link": native_id is not None},
        )
        await _publish_user_meeting_status(
            redis, user_id=user_id, meeting_id=row.get("id"), native_id=native_id,
            status=row.get("status"), when=scheduled_at, log_event=log_event,
        )
        return JSONResponse(status_code=201, content=row)

    # --- PATCH /meetings/{meeting_id} → EDIT a PLANNED meeting by ROW id (title / time / link /
    # workspace / auto_join). Owner-scoped; refused (409) once the row advanced into the bot FSM —
    # the FSM is never fought. `scheduled_at: null` clears the time (status flips to `idle`);
    # `meeting_url: null` detaches the link (row becomes link-less). ---
    # --- native-id → owned ROW resolver (#579 C1). Resolve (platform, native) to the caller's
    # NEWEST OWNED row, exactly the rule the native transcript/authorize paths use (list_meetings is
    # created_at-desc). OWNER-scoped: `shared` rows (a workspace/transcript-share grant) are excluded
    # so a viewer can never mutate/delete someone else's meeting via the native path. None → 404. ---
    async def _resolve_owned_native(user_id: int, platform: str, native_meeting_id: str):
        meetings = await store.list_meetings(user_id, platform=platform)
        for m in meetings:  # newest-first
            if (not m.get("shared")
                    and m.get("platform") == platform
                    and m.get("native_meeting_id") == native_meeting_id):
                return m.get("id")
        return None

    # --- the ROW-id PATCH/DELETE bodies, factored out so the native-keyed aliases (#579 C1) forward
    # to the SAME owner-scoped, FSM-refusing logic once they have resolved (platform, native) → row. ---
    async def _apply_meeting_patch(user_id: int, meeting_id: int, payload) -> dict:
        from .projection import project_response_data

        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="body must be an object")

        updates: dict = {}
        if "title" in payload:
            title = payload["title"]
            if title is not None and not isinstance(title, str):
                raise HTTPException(status_code=422, detail="'title' must be a string")
            updates["title"] = (title or "").strip()[:512] or None
        if "scheduled_at" in payload:
            scheduled_at = payload["scheduled_at"]
            if scheduled_at is not None and not isinstance(scheduled_at, str):
                raise HTTPException(status_code=422, detail="'scheduled_at' must be an ISO8601 string")
            updates["scheduled_at"] = scheduled_at
        if "meeting_url" in payload:
            meeting_url = payload["meeting_url"]
            if meeting_url is not None and not isinstance(meeting_url, str):
                raise HTTPException(status_code=422, detail="'meeting_url' must be a string")
            meeting_url = (meeting_url or "").strip() or None
            if meeting_url:
                parsed = parse_meeting_url(meeting_url)
                if parsed is None:
                    raise HTTPException(status_code=422, detail="unrecognized 'meeting_url'")
                updates["platform"], updates["native_meeting_id"] = parsed
                updates["constructed_meeting_url"] = meeting_url
            else:
                updates["platform"] = "unknown"
                updates["native_meeting_id"] = None
                updates["constructed_meeting_url"] = None
        if "workspace_id" in payload:
            workspace_id = payload["workspace_id"]
            if workspace_id is not None and not isinstance(workspace_id, str):
                raise HTTPException(status_code=422, detail="'workspace_id' must be a string")
            updates["workspace_id"] = (workspace_id or "").strip() or None
        if "auto_join" in payload:
            if not isinstance(payload["auto_join"], bool):
                raise HTTPException(status_code=422, detail="'auto_join' must be a boolean")
            updates["auto_join"] = payload["auto_join"]
            # A per-meeting choice, marked as the user's: calendar sync derives `auto_join` from
            # the connected calendars' policy on every pass, and stands down on a marked row.
            updates["auto_join_user_set"] = True
        if not updates:
            raise HTTPException(status_code=422, detail="no editable fields in body")

        row = await store.update_planned_meeting(user_id, meeting_id, updates)
        if row is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if row.get("error") == "conflict":
            raise HTTPException(
                status_code=409, detail="Meeting is no longer planned (bot lifecycle owns it)"
            )
        if row.get("error") == "duplicate":
            raise HTTPException(status_code=409, detail="Another active meeting uses that link")
        log_event(
            "meeting_plan_updated", audience="user", span="meetings.plan.update",
            user_id=user_id, meeting_id=str(meeting_id),
            fields={"keys": sorted(updates.keys()), "status": row.get("status")},
        )
        await _publish_user_meeting_status(
            redis, user_id=user_id, meeting_id=meeting_id,
            native_id=row.get("native_meeting_id"), status=row.get("status"),
            when=(row.get("data") or {}).get("scheduled_at"), log_event=log_event,
        )
        # The raw ICS event snapshot never rides a response — on ANY read path, and the PATCH's
        # echo of the updated row is one (measured live 2026-08-17: a PATCH reply carried the
        # source's uid + event.resolved_start/calendar/component). Projected HERE, at the response
        # edge, so both the row-id and the native-keyed alias get it and the STORED row — which
        # calendar sync reconciles against — keeps the snapshot.
        #
        # `viewer_is_owner=True` is a property of the write, not an assumption: `update_planned_meeting`
        # selects `WHERE Meeting.id == meeting_id AND Meeting.user_id == user_id` (the fake mirrors it
        # with the same guard) and returns None otherwise, so a non-owner never reaches this line —
        # PATCH has no share or workspace branch. The echo is the owner reading their own row back.
        return {**row, "data": project_response_data(row.get("data"), viewer_is_owner=True)}

    async def _apply_meeting_delete(user_id: int, meeting_id: int) -> dict:
        result = await store.delete_planned_meeting(user_id, meeting_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if result is False:
            plan = await store.prepare_completed_artifact_deletion(user_id, meeting_id)
            if plan is None:
                raise HTTPException(status_code=404, detail="Meeting not found")
            if plan.get("error") == "conflict":
                raise HTTPException(
                    status_code=409,
                    detail="Meeting artifacts can only be deleted after the lifecycle is terminal",
                )

            recordings = list(plan.get("recordings") or [])
            if recordings and artifact_object_deleter is None:
                raise HTTPException(status_code=503, detail="Artifact storage deletion unavailable")
            deleted_objects = 0
            for recording in recordings:
                # Storage FIRST. Any exception deliberately aborts before DB paths/transcripts are
                # scrubbed, so the same owner-scoped request can retry with the original keys.
                deleted_objects += len(await artifact_object_deleter(recording))

            finalized = await store.finalize_completed_artifact_deletion(user_id, meeting_id)
            if finalized is None:
                raise HTTPException(status_code=404, detail="Meeting not found")
            if finalized is False:
                raise HTTPException(
                    status_code=409,
                    detail="Meeting artifacts can only be deleted after the lifecycle is terminal",
                )
            log_event(
                "meeting_artifacts_deleted", audience="user", span="meetings.artifacts.delete",
                user_id=user_id, meeting_id=str(meeting_id),
                fields={"recordings": len(recordings), "objects": deleted_objects,
                        "already_deleted": bool(plan.get("already_deleted"))},
            )
            return {
                "kind": "artifacts", "objects_deleted": deleted_objects,
                "already_deleted": bool(plan.get("already_deleted")),
            }
        log_event(
            "meeting_plan_deleted", audience="user", span="meetings.plan.delete",
            user_id=user_id, meeting_id=str(meeting_id), fields={},
        )
        await _publish_user_meeting_status(
            redis, user_id=user_id, meeting_id=meeting_id, native_id=None,
            status="deleted", when=None, log_event=log_event,
        )
        return {"kind": "plan"}

    @router.patch("/meetings/{meeting_id}")
    async def patch_planned_meeting(
        meeting_id: int,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        row = await _apply_meeting_patch(user_id, meeting_id, payload)
        return JSONResponse(content=row)

    # --- DELETE /meetings/{meeting_id} → delete a PLANNED row (intent status only; an FSM row is
    # never deletable from here). Owner-scoped, ROW-id addressed. ---
    @router.delete("/meetings/{meeting_id}", status_code=204)
    async def delete_planned_meeting(
        meeting_id: int,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        await _apply_meeting_delete(user_id, meeting_id)
        return Response(status_code=204)

    # --- native-keyed PATCH/DELETE /meetings/{platform}/{native_meeting_id} (#579 C1) — the sealed
    # api.v1 mutate routes a 0.10 client (incl. the shipped dashboard) calls. Resolve (platform,
    # native) → the caller's newest OWNED row, then forward to the SAME row-id logic above (which
    # refuses an FSM-owned row with 409). Unknown/unowned native → 404. Additive: the int routes are
    # unchanged. DELETE returns 200 + a small body (the sealed native-delete response), NOT the 204
    # the row-id route returns. ---
    # --- POST /meetings/{platform}/{native_meeting_id}/annotate → the caller's OWN description of
    # a meeting: `title` and arbitrary `metadata`. Works in ANY status, unlike the PATCH below.
    #
    # The split is by WHAT is written, not when. PATCH edits the INSTRUCTIONS for a meeting (url,
    # schedule, auto-join) and is refused once the FSM owns the row, because changing dispatch
    # parameters under a running bot fights it. Annotations are the caller's DESCRIPTION: nothing
    # in the pipeline reads them, so writing them can never re-arm, re-dispatch or re-route
    # anything — and the moments a description is most worth writing are exactly the ones the FSM
    # owns. Mid-meeting ("this is the Acme renewal call") and after it ends (the agent's own
    # summary) were both previously impossible: PATCH answered 409 for the entire useful life of
    # a meeting.
    #
    # `metadata` ALWAYS merges key-wise; an explicit null deletes exactly one key. There is
    # deliberately NO whole-object replace: every writer on an account shares one API key, so a
    # replace would let any caller destroy annotations written by another agent — or by the human
    # — that it never saw and could not have known about. Merge plus explicit nulls expresses
    # every legitimate edit while making "corrupt what you did not write" unrepresentable.
    @router.post("/meetings/{platform}/{native_meeting_id}/annotate")
    async def annotate_native_meeting(
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

        title = payload.get("title")
        if title is not None and not isinstance(title, str):
            raise HTTPException(status_code=422, detail="'title' must be a string")
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise HTTPException(status_code=422, detail="'metadata' must be an object")
        if title is None and metadata is None:
            raise HTTPException(
                status_code=422,
                detail="nothing to annotate: send 'title' and/or 'metadata'",
            )

        meeting_id = await _resolve_owned_native(user_id, platform, native_meeting_id)
        if meeting_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        row = await store.annotate_meeting(user_id, meeting_id, title=title, metadata=metadata)
        if row is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if isinstance(row, dict) and row.get("error") == "metadata_too_large":
            # 413, not 422: the request is well-formed, it is the SIZE that is refused. Nothing is
            # written — a partial store would be worse than the refusal.
            raise HTTPException(status_code=413, detail=row.get("detail", "metadata too large"))
        log_event(
            "meeting_annotated", audience="user", span="meetings.annotate",
            user_id=user_id, meeting_id=str(meeting_id),
            fields={"title": title is not None,
                    "metadata_keys": sorted(metadata.keys()) if metadata else [],
                    "status": row.get("status")},
        )
        return JSONResponse(content=row)

    @router.patch("/meetings/{platform}/{native_meeting_id}")
    async def patch_native_meeting(
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
        meeting_id = await _resolve_owned_native(user_id, platform, native_meeting_id)
        if meeting_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        row = await _apply_meeting_patch(user_id, meeting_id, payload)
        return JSONResponse(content=row)

    @router.delete("/meetings/{platform}/{native_meeting_id}")
    async def delete_native_meeting(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meeting_id = await _resolve_owned_native(user_id, platform, native_meeting_id)
        if meeting_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        receipt = await _apply_meeting_delete(user_id, meeting_id)
        body = {
            "status": "deleted", "id": meeting_id,
            "platform": platform, "native_meeting_id": native_meeting_id,
        }
        if receipt["kind"] == "artifacts":
            body.update({
                "deleted": "completed_meeting_artifacts",
                "objects_deleted": receipt["objects_deleted"],
                "backup_residuals": "expire_under_deployment_retention_policy",
            })
        return JSONResponse(content=body)

    # --- GET /bots/{platform}/{native_meeting_id}/chat (#579 C3, sealed api.v1 ChatMessagesResponse).
    # Thin HONEST restore: the route + owner boundary are real (unowned/unknown native → 404), but
    # 0.12 does not PERSIST in-meeting chat server-side (chat frames flow live over the va:…:chat WS
    # channel and are not stored), so the captured-message list is always empty until a chat-capture
    # backend lands. The response conforms to the sealed shape; the empty list is the truthful state,
    # not a fabricated one. The POST (send) half is a SIGNED GAP — see the PR (no bot-command backend
    # in the 0.12 core). ---
    @router.get("/bots/{platform}/{native_meeting_id}/chat")
    async def read_meeting_chat(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meeting_id = await _resolve_owned_native(user_id, platform, native_meeting_id)
        if meeting_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )
        return JSONResponse(content={"messages": []})

    # --- GET /meetings/{platform}/{native_meeting_id}/participants → who was in this meeting, as far as
    # the 0.12 core actually KNOWS. Owner-scoped (404 on someone else's meeting — never an empty roster,
    # which would confirm the row exists across a tenant boundary).
    #
    # The honest shape matters more than the convenient one. Two sources are persisted today and they
    # answer DIFFERENT questions, so every row says which one it came from:
    #   `invite`  — the calendar invitation's ATTENDEE lines (meeting.data['attendees']). Who was ASKED.
    #               Includes people who never spoke — but only when the meeting arrived via a connected
    #               calendar feed, and an invitee who no-showed still appears.
    #   `speaker` — DISTINCT transcriptions.speaker. Who was HEARD and named. A silent participant is
    #               absent by construction; so is a speaker whose platform tile never yielded a name.
    #
    # Neither is attendance, and this route does NOT pretend otherwise: `observed_roster` is the flat
    # statement that nobody recorded who was actually present. The 0.12 platform modules observe only
    # tiles emitting a SPEAKING signal (msteams-speakers.ts's `hasRequiredSignal` gate; gmeet-speakers'
    # `t.speaking` filter), and no producer writes a roster to any store — there is no `participants`
    # table and `lifecycle.v1`'s `speaker_events` has had no writer since the 0.12 cutover
    # (Vexa-ai/vexa#861). So an EMPTY `participants` here means "we never recorded this", which is why
    # the field is present and constant rather than absent: a consumer must be able to tell "nobody
    # attended" (never true from this data) from "attendance was never captured" (always true today).
    #
    # NO identity resolution is done. A person who was both invited and heard appears TWICE, once per
    # source, because matching a voice label to an invitee is a guess and the wrong guess silently
    # merges two humans. #861's preparation forbids promoting transcript speakers into a roster; keeping
    # the sources side by side is how this route obeys that while still answering the question. ---
    @router.get("/meetings/{platform}/{native_meeting_id}/participants")
    async def get_meeting_participants(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        found = await store.get_meeting_participants(user_id, platform, native_meeting_id)
        if found is None:
            log_event(
                "participants_not_found", audience="system", level="warning",
                span="meetings.participants", user_id=user_id,
                meeting_id=f"{platform}/{native_meeting_id}",
            )
            raise HTTPException(
                status_code=404,
                detail=f"Meeting not found for platform {platform} and ID {native_meeting_id}",
            )

        participants: list[dict] = []
        for entry in found.get("invited") or []:
            if not isinstance(entry, dict):
                continue
            email = entry.get("email")
            row = {
                "name": entry.get("name") or None,
                "email": email if isinstance(email, str) and email else None,
                "source": "invite",
            }
            # PARTSTAT rides through as the invitee's own answer; absent when the feed carried none.
            partstat = entry.get("partstat")
            if isinstance(partstat, str) and partstat.strip():
                row["response_status"] = partstat.strip().lower()
            participants.append(row)
        for name in found.get("speakers") or []:
            participants.append({"name": name, "email": None, "source": "speaker"})

        sources = sorted({p["source"] for p in participants})
        log_event(
            "participants_served", audience="user", span="meetings.participants",
            user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
            fields={"count": len(participants), "sources": ",".join(sources)},
        )
        return JSONResponse(content={
            "meeting_id": found["meeting_id"],
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "participants": participants,
            "sources": sources,
            # Constant on 0.12 — see the block above. Becomes meaningful when a roster producer exists.
            "observed_roster": "not_recorded",
        })

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

    def _share_payload(payload) -> "tuple[str, list, int]":
        """mode | allowed_emails | ttl out of a share-mint body, defaulted the same way for both
        address shapes. `restricted` + allowed_emails is what makes a forwarded mail grant nothing."""
        if not isinstance(payload, dict):
            payload = {}
        return (
            str(payload.get("mode", "open")).strip() or "open",
            payload.get("allowed_emails") or [],
            int(payload.get("expires_in_sec", 86400) or 86400),
        )

    # --- POST /meetings/{meeting_id}/transcript-import → complete a meeting FROM A TRANSCRIPT.
    #
    # The product feature is "import a transcript": a meeting that already happened somewhere we did
    # not record — a Zoom export, a TSC recording, minutes from a call nobody sent a bot to — becomes
    # a first-class Vexa meeting. Everything downstream then treats it as one: the canvas, the by-id
    # transcript read, search, the post-meeting flows.
    #
    # It is ALSO the honest capture double. The rehearsal rig used to build this row by hand —
    # `docker exec … psql` INSERTing `transcriptions` and UPDATEing `meetings` with the DB password
    # it read out of another container — because meeting-api owned the two things the double needed
    # and exposed neither: a way to put words on a row, and a way to say WHEN the meeting was (a bot
    # run stamps start/end from `now()`, so a call that happened last Tuesday was inexpressible).
    # That shell-out is the audit's V4/N5 — a second writer on a table this service owns, building
    # SQL by string-interpolating speaker names, and it produced rows the product never makes
    # (`scheduled`, NULL start/end) which read as "UPCOMING" over a finished meeting. One route
    # closes the feature gap and the ownership hole at once, which is why `source` is declared
    # rather than inferred: a reader can always tell a double from a real import.
    #
    # ROW ID, not the (platform, native) pair — the same lesson the by-id share mint above records:
    # the pair is not an identity, and the rows most likely to be imported into are exactly the ones
    # no pair addresses. Three segments against the pair routes' four, so neither can shadow the
    # other; registered before them anyway, matching the `by-id` precedent.
    #
    # Owner-scoped, and refused (409) while a bot is in flight on the row — the FSM is never fought.
    @router.post("/meetings/{meeting_id}/transcript-import")
    async def import_transcript(
        meeting_id: int,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        from .transcript_import import (SOURCES, normalize_segments, occurrence_window,
                                        session_uid_for)

        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="body must be an object")

        source = str(payload.get("source") or "import").strip()
        if source not in SOURCES:
            raise HTTPException(
                status_code=422,
                detail=f"'source' must be one of {sorted(SOURCES)} — say where the transcript came from",
            )
        session_uid = session_uid_for(source, meeting_id)
        segments, reason = normalize_segments(payload.get("segments"), session_uid)
        if reason:
            raise HTTPException(status_code=422, detail=reason)
        started_at, ended_at, reason = occurrence_window(
            segments, payload.get("started_at"), payload.get("ended_at"),
        )
        if reason:
            raise HTTPException(status_code=422, detail=reason)

        result = await store.complete_transcript_import(
            user_id, meeting_id, segments=segments, started_at=started_at, ended_at=ended_at,
            source=source, session_uid=session_uid,
        )
        if result is None:
            log_event(
                "transcript_import_failed", audience="system", level="warning",
                span="meetings.transcript.import", user_id=user_id, meeting_id=str(meeting_id),
                fields={"source": source, "reason": "no such meeting for this owner"},
            )
            raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")
        if result.get("error") == "conflict":
            raise HTTPException(
                status_code=409,
                detail=(f"Meeting {meeting_id} is {result.get('status')} — a bot is in flight on it. "
                        "A live meeting is completed by its bot, not by an import."),
            )

        if result.get("imported"):
            # What a real completion emits, emitted here for the same reason: the terminal learns a
            # meeting's status from the `u:{user}:meetings` frame the gateway `/ws` forwards
            # (surfaces/gatewayWS.ts), and its live view of a meeting ends on the `session_end`
            # marker the collector appends to `tc:meeting:{id}` (ingest._transcript_stream). Both are
            # best-effort, like every other publish on this path — the durable row is the truth.
            #
            # What is deliberately NOT emitted: the segments themselves, into the live stream. They
            # went through it during a real call because the call was happening. Replaying an
            # already-finished transcript as live frames would be a fake liveness, and the canvas
            # reads a completed meeting from `GET /transcripts/by-id/{id}` anyway.
            await _publish_user_meeting_status(
                redis, user_id=user_id, meeting_id=result.get("meeting_id"),
                native_id=result.get("native_meeting_id"), status="completed",
                when=result.get("end_time"), log_event=log_event,
            )
            if redis is not None:
                try:
                    await redis.xadd(f"tc:meeting:{result['meeting_id']}",
                                     {"type": "session_end", "uid": session_uid})
                except Exception as e:  # noqa: BLE001 — best-effort marker; never fail the import
                    log_event("transcript_import_marker_failed", audience="system", level="warning",
                              span="meetings.transcript.import", user_id=user_id,
                              meeting_id=str(meeting_id), fields={"error": str(e)})

        log_event(
            "transcript_imported", audience="user", span="meetings.transcript.import",
            user_id=user_id, meeting_id=str(meeting_id),
            fields={"source": source, "segments": result.get("segments_imported"),
                    "imported": bool(result.get("imported")),
                    "start_time": result.get("start_time"), "end_time": result.get("end_time")},
        )
        return JSONResponse(content=result)

    # --- POST /meetings/{meeting_id}/share → the SAME mint, addressed by the ROW's primary key.
    #
    # Why it exists: the (platform, native) pair is not an identity. A meeting planned from an invite
    # whose url matched no platform lands as platform='unknown' with an EMPTY platform_specific_id —
    # no pair addresses it, so the pair-keyed mint below answers 404 and every attendee mail for that
    # meeting went out with no capability at all (row 97, 2026-09-02). The row id always exists.
    #
    # Route ordering / shadowing: this path is THREE segments (`meetings/{id}/share`) and the pair
    # route is FOUR — Starlette compiles a full-path regex per route, so on segment count alone
    # neither can swallow the other, the same property the gateway's by-row-id notes rely on. The
    # `meeting_id: int` annotation is belt-and-braces: a non-numeric id is refused by validation here
    # rather than being resolved as some other kind of name. Registered BEFORE the pair route anyway,
    # matching the `by-id` precedent at the top of this file.
    #
    # Owner-scoped: a row that is not the caller's 404s exactly like an unknown one. Minting a
    # capability is an owner act, and a share route that distinguished the two would leak existence.
    @router.post("/meetings/{meeting_id}/share")
    async def mint_transcript_share_by_id(
        meeting_id: int,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        mode, emails, ttl = _share_payload(payload)
        minted = await store.mint_transcript_share_by_id(
            user_id, meeting_id, mode=mode, allowed_emails=emails, expires_in_sec=ttl,
        )
        if minted is None:
            log_event(
                "transcript_share_mint_failed", audience="system", level="warning",
                span="meetings.transcript.share", user_id=user_id, meeting_id=str(meeting_id),
                fields={"mode": mode, "reason": "no such meeting for this owner"},
            )
            raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")
        log_event("transcript_share_minted", audience="user", span="meetings.transcript.share",
                  user_id=user_id, meeting_id=str(meeting_id), fields={"mode": mode, "by": "row_id"})
        return JSONResponse(content=minted)

    # --- POST /meetings/{platform}/{native_meeting_id}/share → mint an INDEPENDENT transcript share link
    # (capability token). Owner-scoped. open|restricted(+allowed_emails), TTL. The token is returned ONCE
    # (only its hash is stored). Redeemed at POST /transcripts/share/accept — NO workspace involved.
    # Kept for 0.10 clients and the /transcripts/{platform}/{native}/share alias; new callers use the
    # by-row-id route above, which can address rows this one cannot. ---
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
        mode, emails, ttl = _share_payload(payload)
        minted = await store.mint_transcript_share(
            user_id, platform, native_meeting_id, mode=mode, allowed_emails=emails, expires_in_sec=ttl,
        )
        if minted is None:
            log_event(
                "transcript_share_mint_failed", audience="system", level="warning",
                span="meetings.transcript.share", user_id=user_id,
                meeting_id=f"{platform}/{native_meeting_id}",
                fields={"mode": mode, "reason": "no meeting addressed by this (platform, native) pair"},
            )
            raise HTTPException(status_code=404, detail=f"Meeting not found for {platform}/{native_meeting_id}")
        log_event("transcript_share_minted", audience="user", span="meetings.transcript.share",
                  user_id=user_id, meeting_id=f"{platform}/{native_meeting_id}",
                  fields={"mode": mode, "by": "pair"})
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
    calendar_sync_now: Optional[Callable] = None,
    calendar_sync_status: Optional[Callable] = None,
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

    app.include_router(build_router(store, redis, log_event=log_event,
                                    calendar_sync_now=calendar_sync_now,
                                    calendar_sync_status=calendar_sync_status))
    return app
