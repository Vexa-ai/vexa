"""``create_app(...) -> FastAPI`` — the ONE uvicorn-able meeting-api modular monolith (P2).

This is the unified meeting-api: ONE FastAPI app composed of front-doored modules, each a
sub-package of ``meeting_api`` mounted here (the v0.12 analog of the parent ``main.py``'s flat
``app.include_router(...)`` list, but each module is an isolated brick behind a port-seam):

  * **lifecycle** — the bot lifecycle callback receiver + meeting-state FSM (lifecycle.v1):
    POST ``/bots/internal/callback/lifecycle`` and POST ``/runtime/callback``, mounted by
    ``lifecycle.mount.mount_lifecycle``.
  * **bot_spawn** — POST ``/bots``: build the invocation.v1 invocation + mint the MeetingToken +
    spawn the meeting-bot over runtime.v1, eager-creating the MeetingSession on spawn.
  * **collector** — the folded-in transcript backend (collector domain):
    GET ``/transcripts/{platform}/{native_meeting_id}``, GET ``/meetings``,
    POST ``/ws/authorize-subscribe`` (+ the ``transcription_segments`` → ``tc:…:mutable`` consumer).
  * **recordings** — POST ``/internal/recordings/upload``, GET ``/recordings``,
    GET ``/recordings/{id}/master`` (chunks + master → ``meeting.data`` JSONB).
  * **obs** — ``TraceMiddleware`` (logevent.v1 trace_id threading) + the shared ``GET /health``.

webhooks + scheduling are library bricks (no HTTP surface of their own in the core path — they are
driven by the lifecycle/bot_spawn flows); they are re-exported from the package front door and wired
by the production composition root in P3. continue_meeting / max-bots / join-retry / the segment
consumer loop are P3 seams.

``create_app`` takes every collaborator as an injected port (or builds a default in-memory stack for
the app factory / tests), so the SAME app runs with real adapters in prod and in-process fakes in
the conformance harness — the conformance assertions therefore drive THIS shipped app.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import bot_spawn as _bot_spawn
from . import identity_token
from . import recordings as _recordings
from . import route_scopes
from .collector.app import build_router as _build_collector_router
from .collector.ports import RedisBus, TranscriptStore
from .lifecycle.machine import LifecycleSink, MeetingStore
from .lifecycle.mount import mount_lifecycle
from .obs import TraceMiddleware


def _xpending_total(summary) -> "Optional[int]":
    """Total DELIVERED-but-un-acked count for the group from an XPENDING SUMMARY reply (#636).
    redis-py returns a dict ``{'pending': N, 'min', 'max', 'consumers'}``; the raw protocol reply is
    a list ``[N, min, max, consumers]``. Returns the integer total, or None when unrecognizable."""
    if isinstance(summary, dict):
        v = summary.get("pending")
        return int(v) if isinstance(v, int) else None
    if isinstance(summary, (list, tuple)) and summary:
        try:
            return int(summary[0])
        except (TypeError, ValueError):
            return None
    return None


async def _pipeline_health(app) -> "tuple[dict, bool]":
    """#527/#636: derive pipeline liveness from the per-loop heartbeats + collector-group lag +
    pending-entry (PEL) depth, and decide whether to DEGRADE. A loop hung inside an await stops
    stamping, so its tick_age_s climbs past PIPELINE_TICK_STALE_S even while the process and the
    live-WS path look healthy — the 2026-04-26 silent hang. A crashed replica's delivered-but-un-acked
    batch is NOT lag (it was delivered) and NOT a stale heartbeat on the survivor, so #636 surfaces it
    as ``pending_depth``. Returns ``({loops, redis_reachable, consumer_lag, pending_depth}, degraded)``.

    #809 — Redis is a CACHE/QUEUE dependency, not the process's spine: an unreachable Redis is
    reported HONESTLY as ``redis_reachable: false`` but NEVER flips ``degraded`` (so it cannot 503 the
    shared probe). DB-backed reads keep serving through a Redis outage, so readiness stays true — a
    cache blip no longer becomes a total core outage (the 2026-07-19 boot-block/CrashLoop).

    The probe MUST NOT itself hang (that would defeat the point): the XINFO/XPENDING calls are each
    bounded by a 2s wait_for and any failure degrades that field to ``"unavailable"`` — never blocks."""
    st = app.state
    now = time.monotonic()
    stale_s = getattr(st, "pipeline_tick_stale_s", 120.0)
    lag_alarm = getattr(st, "pipeline_lag_alarm", 500)
    pending_alarm = getattr(st, "pipeline_pending_alarm", 100)
    loops = {name: round(now - ts, 1) for name, ts in (st.pipeline_ticks or {}).items()}
    degraded = any(age > stale_s for age in loops.values())

    lag = None
    pending_depth = None
    redis_reachable = None
    redis = getattr(st, "pipeline_redis", None)
    if redis is not None:
        # #809: a bounded reachability PING FIRST — the honest per-component signal the 2026-07-19
        # incident lacked (/health read "ok" through a 40-minute Redis outage). A dead Redis fails
        # here within 2s; we then SKIP the stream probes (they would only time out too) and report
        # their fields "unavailable". This NEVER sets `degraded`: Redis is a cache/queue, so the
        # DB-backed readiness paths stay green and the shared probe stays 200 (no CrashLoop recurrence).
        # `ping` is resolved defensively: a client without it (a minimal probe stub) leaves
        # reachability UNKNOWN (None) and the stream probes run exactly as before — the real
        # ``redis.asyncio`` client always has ``ping``, so production always gets the honest signal.
        ping = getattr(redis, "ping", None)
        if ping is not None:
            try:
                await asyncio.wait_for(ping(), timeout=2.0)
                redis_reachable = True
            except Exception:
                redis_reachable = False
    # Run the stream probes unless we KNOW Redis is down (reachable is False). Unknown (None, no
    # ping) or True → probe, preserving the pre-#809 lag/pending behaviour.
    if redis is not None and redis_reachable is not False:
        try:
            groups = await asyncio.wait_for(redis.xinfo_groups(st.pipeline_stream), timeout=2.0)
            for g in groups or []:
                name = g.get("name") if isinstance(g, dict) else None
                name = name.decode() if isinstance(name, (bytes, bytearray)) else name
                if name == st.pipeline_group:
                    lag = g.get("lag")
                    break
        except Exception:
            lag = "unavailable"  # a dead/absent group is itself a signal, never a hang
        # #636: PEL depth — a bounded XPENDING SUMMARY. A delivered-but-un-acked orphan is invisible
        # to lag; a SUSTAINED non-zero total is the orphan signal (steady state acks within a tick).
        try:
            summary = await asyncio.wait_for(
                redis.xpending(st.pipeline_stream, st.pipeline_group), timeout=2.0
            )
            pending_depth = _xpending_total(summary)
            if pending_depth is None:
                pending_depth = "unavailable"
        except Exception:
            pending_depth = "unavailable"  # never block the probe on a pending read
    elif redis is not None and redis_reachable is False:
        # #809: Redis unreachable → the stream signals are unavailable, but readiness is NOT degraded.
        lag = "unavailable"
        pending_depth = "unavailable"
    if isinstance(lag, int) and lag > lag_alarm:
        degraded = True
    if isinstance(pending_depth, int) and pending_depth > pending_alarm:
        degraded = True
    return {
        "loops": loops,
        "redis_reachable": redis_reachable,
        "consumer_lag": lag,
        "pending_depth": pending_depth,
    }, degraded


def create_app(
    *,
    # collector ports
    transcript_store: Optional[TranscriptStore] = None,
    redis: Optional[RedisBus] = None,
    # bot_spawn ports
    meeting_repo: Optional["_bot_spawn.MeetingRepo"] = None,
    runtime: Optional["_bot_spawn.RuntimeClient"] = None,
    service_authority: Optional["object"] = None,
    # recordings ports
    recording_repo: Optional["_recordings.RecordingRepo"] = None,
    storage: Optional["_recordings.Storage"] = None,
    # lifecycle store
    meeting_store: Optional[MeetingStore] = None,
    token_secret: Optional[str] = None,
    # user-stop (DELETE /bots) redis command publisher
    command_publisher: Optional["object"] = None,
    # per-user webhook delivery sink (WebhookSink) — delivers meeting.status_change on each FSM advance
    webhook_sink: Optional["object"] = None,
    # operator-owned terminal callback — boot-frozen destination, never user/meeting input
    system_webhook_sink: Optional["object"] = None,
    # per-user delivery ledger (#841) — the queryable record GET /webhooks/deliveries reads. The
    # lifecycle callback records each delivery outcome here so the dashboard's Delivery History
    # reflects real deliveries, not just the Test button. None → in-memory fake (app-factory/tests).
    delivery_ledger: Optional["object"] = None,
    # completion finalizer — awaited with the NUMERIC meeting id when the FSM lands on a TERMINAL
    # status (completed/failed). Production wires collector/db_writer.finalize_meeting: flush the
    # meeting's remaining redis segments to Postgres + persist the processed doc into meeting.data,
    # so a finished meeting's transcript is durable IMMEDIATELY. Best-effort — never fails the callback.
    transcript_finalizer: Optional["object"] = None,
    # calendar-sync user edges (async callables from the composition root; None → routes 503)
    calendar_sync_now: Optional["object"] = None,
    calendar_sync_status: Optional["object"] = None,
    # gateway-identity.v1 — the gateway's Ed25519 PUBLIC key, and the internal tier's secret. With a
    # key, every x-user-* header must be signed by the gateway or carried by the internal tier, or
    # the request is refused before any route reads it. The production entrypoint always passes one
    # (the boot refuses without it); None is the in-process harness, which drives the routes directly.
    identity_key=None,
    internal_secret: Optional[str] = None,
    # Defines each bot's own Redis user (bot_spawn.workload_redis.BotRedisUsers): spawns hand the bot
    # that user's URL and a terminal session removes it. None hands bots the service Redis URL — the
    # in-process harness, and a deployment's explicit REDIS_WORKLOAD_ACL=shared.
    bot_redis: Optional["object"] = None,
    # The runtime caller credential: a /runtime/callback must carry the runtime's signature over its
    # event, keyed from it (runtime_signature). The production entrypoint always passes it.
    runtime_callback_token: Optional[str] = None,
    # The in-process harness's explicit opt-in to drive the bot and runtime callbacks with no key
    # wired. Without it, a callback door whose key (token_secret / runtime_callback_token) is unset
    # refuses every caller with 401. The production entrypoint never sets it.
    open_callbacks: bool = False,
) -> FastAPI:
    """Build the unified meeting-api app from the injected ports.

    Any port left ``None`` falls back to its in-memory fake so the app factory stands up a fully
    in-process meeting-api (no DB, no redis, no MinIO, no runtime kernel) — the shape the unified
    health + conformance harnesses drive. Production wires the real adapters via each module's
    ``adapters.build_production_*`` (composition is P3; the seams are here).
    """
    # THE EDGE'S SCOPES, CHECKED AGAIN ON THE ROUTE A REQUEST MATCHED (route_scopes). A request
    # carrying the gateway's signed identity is refused when its key holds none of the scopes of the
    # meetings rows that reach this route, or when no row reaches it at all — so a hop that landed
    # on a route other than the one the edge checked is refused here too. App-level, so every route
    # is covered and none asks for it by hand.
    app = FastAPI(title="Vexa Meeting API (v0.12)", version="0.12.0",
                  dependencies=[route_scopes.SCOPE_GATE])
    # The edge: read/mint X-Trace-Id and bind it for the request (logevent.v1 trace_id).
    app.add_middleware(TraceMiddleware)
    # THE DOOR FOR x-user-* (gateway-identity.v1). meeting-api derives the owner, the bot limit, the
    # workspace memberships and the webhook from these headers; a request that names a person
    # without the gateway's signature or the internal tier is refused here. Bot and runtime
    # callbacks name no person and pass through to the routes that authenticate them.
    if identity_key is not None:
        app.add_middleware(identity_token.IdentityGuard, verify_key=identity_key,
                           internal_secret=internal_secret or "", service="meeting-api")

    # --- shared liveness probe (gate:health): the unified process is up. No auth. The ADDITIVE
    # `capabilities` rows are the config.v1 tri-states (stt · object_storage) incl. the cached STT
    # live auth probe (ADR-0026) — existing consumers key on `status` only and keep working; the
    # rows never flip `status` (an unconfigured capability degrades a FEATURE, not the process). ---
    @app.get("/health")
    async def health():
        from .config_preflight import capability_health

        body = {"status": "ok", "service": "meeting-api", "capabilities": capability_health()}
        # #527: additive `pipeline` section — present ONLY when the background loops are wired
        # (build_production_app sets app.state.pipeline_ticks). On the bare app-factory path (unit
        # tests, conformance) the section is omitted and status stays "ok" — existing /health
        # consumers are unchanged. A stale loop or a lag over threshold flips status→degraded + 503,
        # so a dead pipeline that keeps the live-WS path flowing no longer looks healthy.
        if getattr(app.state, "pipeline_ticks", None) is not None:
            pipeline, degraded = await _pipeline_health(app)
            body["pipeline"] = pipeline
            if degraded:
                body["status"] = "degraded"
                return JSONResponse(body, status_code=503)
        return body

    # --- bot_spawn ports (resolved FIRST: the meeting_repo is also the lifecycle-persistence target) ---
    if meeting_repo is None:
        meeting_repo = _bot_spawn_fakes().InMemoryMeetingRepo()
    if runtime is None:
        runtime = _bot_spawn_fakes().FakeRuntimeClient()
    if service_authority is None:
        from .service_authority import AllowAllServiceAuthority

        service_authority = AllowAllServiceAuthority()
    app.state.service_authority = service_authority

    # --- lifecycle: bot lifecycle callbacks + FSM (lifecycle.v1), PERSISTED to the meeting row ---
    sink = LifecycleSink(store=meeting_store if meeting_store is not None else MeetingStore())
    app.state.lifecycle_sink = sink
    app.state.lifecycle_store = sink.store
    app.state.webhook_sink = webhook_sink
    # #841: the per-user delivery ledger the read endpoint serves. Default to the in-memory fake so
    # the app-factory / conformance path stands up without redis (same pattern as the other ports).
    if delivery_ledger is None:
        from .webhooks import InMemoryDeliveryLedger

        delivery_ledger = InMemoryDeliveryLedger()
    app.state.delivery_ledger = delivery_ledger
    # The lifecycle callback publishes each persisted FSM advance to bm:meeting:{id}:status so the
    # gateway /ws (which SUBSCRIBEs that channel) forwards a ws.v1 BotStatus frame to the dashboard.
    mount_lifecycle(
        app,
        sink,
        meeting_repo,
        webhook_sink,
        system_webhook_sink,
        redis,
        transcript_finalizer,
        delivery_ledger,
        callback_secret=token_secret,
        internal_secret=internal_secret,
        bot_redis=bot_redis,
        runtime_callback_token=runtime_callback_token,
        open_callbacks=open_callbacks,
    )

    # --- bot_spawn: POST /bots (invocation.v1 + runtime.v1) ---
    # A fresh meeting must start on an EMPTY transcript stream. Wire a redis-backed purge (None offline)
    # so bot_spawn can clear tc:meeting:{new_row_id} on a FRESH insert — a reused row id (e.g. after a DB
    # id-sequence reset without a redis flush) would otherwise carry a prior generation's stale
    # session_end and the terminal would show "Meeting ended" before the first word. continue_meeting is
    # untouched (it keeps the reused row's stream). See bot_spawn.service.request_bot.
    #
    # No `fetch_bot_context` here: `request_bot` resolves this person's default bot name out of the
    # spawn context it already fetches for the transcription backend and the capture-signal
    # decision. Injecting a second fetcher made POST /bots ask identity for the same body twice.
    _stream_purge = None
    app.include_router(_bot_spawn.build_router(
        meeting_repo,
        runtime,
        service_authority,
        transcript_stream_purge=_stream_purge,
        redis_grant=bot_redis.grant if bot_redis is not None else None,
    ))

    # --- user-stop: DELETE /bots/{platform}/{native_meeting_id} (lifecycle/stop.py over redis) ---
    from .lifecycle.stop_router import InMemoryCommandPublisher, build_stop_router

    if command_publisher is None:
        command_publisher = InMemoryCommandPublisher()
    app.state.command_publisher = command_publisher
    # The stop router also gets the runtime client so a stop can directly tear down a still-booting bot's
    # workload (the leave command alone is fire-and-forget — a booting bot may never receive it → orphan).
    app.include_router(build_stop_router(meeting_repo, command_publisher, runtime))

    # Resolve the shared recording storage before mounting the collector: completed-meeting erasure
    # uses this same port to delete objects before its transcript/JSONB finalization.
    if storage is None:
        storage = _recordings_fakes().InMemoryStorage()

    async def _delete_recording_objects(recording: dict) -> list[str]:
        from .recordings.deletion import delete_recording_objects

        return await delete_recording_objects(storage, recording)

    async def _delete_meeting_fixtures(user_id: int, meeting_id: int) -> list[str]:
        from .recordings.deletion import delete_meeting_fixtures
        return await delete_meeting_fixtures(storage, user_id=user_id, meeting_id=meeting_id)

    # --- collector: transcripts + meetings + ws-authorize (api.v1) ---
    if transcript_store is None:
        transcript_store = _collector_fakes().InMemoryTranscriptStore()
    app.include_router(_build_collector_router(transcript_store, redis,
                                            calendar_sync_now=calendar_sync_now,
                                            calendar_sync_status=calendar_sync_status,
                                            artifact_object_deleter=_delete_recording_objects,
                                            fixture_object_deleter=_delete_meeting_fixtures))

    # --- recordings: chunk upload + finalize → meeting.data JSONB (recording.v1) ---
    if recording_repo is None:
        recording_repo = _recordings_fakes().InMemoryRecordingRepo()
    app.include_router(_recordings.build_router(recording_repo, storage, token_secret=token_secret))

    # meeting-bundle.v1: export a meeting the caller owns as one portable file, import one as a new
    # meeting. Composed over the same three ports as the routes above — the transcript store, the
    # recording repo and object storage — so an import lands every byte through its one writer.
    from . import bundle as _bundle
    from .obs import log_event as _log_event

    app.include_router(_bundle.build_router(
        transcript_store, recording_repo, storage,
        finalize=_recordings.finalize_master, log_event=_log_event, secret=token_secret,
    ))

    # --- session_profile: PUT /internal/browser-session/{session_uid} — the authenticated bot's write-back ---
    from .session_profile import build_router as _build_session_profile_router
    app.include_router(_build_session_profile_router(meeting_repo, token_secret=token_secret))

    # --- webhooks: GET /webhooks/deliveries — the per-user delivery history the dashboard reads (#841) ---
    app.include_router(_build_webhooks_router(delivery_ledger))

    return app


# ── webhooks read surface (#841): the queryable delivery ledger the dashboard's history reads ────


def _build_webhooks_router(delivery_ledger: "object") -> "object":
    """``GET /webhooks/deliveries`` — the per-user webhook delivery history (#841).

    Owner-scoped via ``X-User-Id`` (the gateway injects it from the resolved key; the client never
    sets it). Returns ``{deliveries: [...]}`` newest-first — each row is the #817 outcome taxonomy
    (host only, never a URL or secret; P14). This is the user-facing completion of #815→#817:
    the dispatcher records every outcome here, so real deliveries appear in Delivery History, not
    just the dashboard's own Test button.
    """
    from fastapi import APIRouter, Header, Query

    router = APIRouter()

    @router.get("/webhooks/deliveries")
    async def list_deliveries(
        x_user_id: Optional[str] = Header(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        user_id = x_user_id
        deliveries = await delivery_ledger.list(user_id, limit=limit) if user_id else []
        return {"deliveries": deliveries}

    return router


# ── lazy fake imports (keep the default in-memory stack off the prod import path) ────────────────


def _bot_spawn_fakes():
    from .bot_spawn import fakes

    return fakes


def _collector_fakes():
    from .collector import fakes

    return fakes


def _recordings_fakes():
    from .recordings import fakes

    return fakes
