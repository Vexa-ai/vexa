"""``create_app(...) -> FastAPI`` — the ONE uvicorn-able meeting-api modular monolith (P2).

This is the unified meeting-api: ONE FastAPI app composed of front-doored modules, each a
sub-package of ``meeting_api`` mounted here (the v0.12 analog of the parent ``main.py``'s flat
``app.include_router(...)`` list, but each module is an isolated brick behind a port-seam):

  * **lifecycle** — the bot lifecycle callback receiver + meeting-state FSM (lifecycle.v1):
    POST ``/bots/internal/callback/lifecycle``.
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

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import bot_spawn as _bot_spawn
from . import recordings as _recordings
from .collector.app import build_router as _build_collector_router
from .collector.ports import RedisBus, TranscriptStore
from .lifecycle.machine import LifecycleSink, MeetingStore
from .obs import TraceMiddleware


def create_app(
    *,
    # collector ports
    transcript_store: Optional[TranscriptStore] = None,
    redis: Optional[RedisBus] = None,
    # bot_spawn ports
    meeting_repo: Optional["_bot_spawn.MeetingRepo"] = None,
    runtime: Optional["_bot_spawn.RuntimeClient"] = None,
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
    # completion finalizer — awaited with the NUMERIC meeting id when the FSM lands on a TERMINAL
    # status (completed/failed). Production wires collector/db_writer.finalize_meeting: flush the
    # meeting's remaining redis segments to Postgres + persist the processed doc into meeting.data,
    # so a finished meeting's transcript is durable IMMEDIATELY. Best-effort — never fails the callback.
    transcript_finalizer: Optional["object"] = None,
) -> FastAPI:
    """Build the unified meeting-api app from the injected ports.

    Any port left ``None`` falls back to its in-memory fake so the app factory stands up a fully
    in-process meeting-api (no DB, no redis, no MinIO, no runtime kernel) — the shape the unified
    health + conformance harnesses drive. Production wires the real adapters via each module's
    ``adapters.build_production_*`` (composition is P3; the seams are here).
    """
    app = FastAPI(title="Vexa Meeting API (v0.12)", version="0.12.0")
    # The edge: read/mint X-Trace-Id and bind it for the request (logevent.v1 trace_id).
    app.add_middleware(TraceMiddleware)

    # --- shared liveness probe (gate:health): the unified process is up. No auth. The ADDITIVE
    # `capabilities` rows are the config.v1 tri-states (stt · object_storage) incl. the cached STT
    # live auth probe (ADR-0026) — existing consumers key on `status` only and keep working; the
    # rows never flip `status` (an unconfigured capability degrades a FEATURE, not the process). ---
    @app.get("/health")
    async def health():
        from .config_preflight import capability_health

        return {"status": "ok", "service": "meeting-api", "capabilities": capability_health()}

    # --- bot_spawn ports (resolved FIRST: the meeting_repo is also the lifecycle-persistence target) ---
    if meeting_repo is None:
        meeting_repo = _bot_spawn_fakes().InMemoryMeetingRepo()
    if runtime is None:
        runtime = _bot_spawn_fakes().FakeRuntimeClient()

    # --- lifecycle: bot lifecycle callbacks + FSM (lifecycle.v1), PERSISTED to the meeting row ---
    sink = LifecycleSink(store=meeting_store if meeting_store is not None else MeetingStore())
    app.state.lifecycle_sink = sink
    app.state.lifecycle_store = sink.store
    app.state.webhook_sink = webhook_sink
    # The lifecycle callback publishes each persisted FSM advance to bm:meeting:{id}:status so the
    # gateway /ws (which SUBSCRIBEs that channel) forwards a ws.v1 BotStatus frame to the dashboard.
    _mount_lifecycle(app, sink, meeting_repo, webhook_sink, redis, transcript_finalizer)

    # --- bot_spawn: POST /bots (invocation.v1 + runtime.v1) ---
    app.include_router(_bot_spawn.build_router(meeting_repo, runtime))

    # --- user-stop: DELETE /bots/{platform}/{native_meeting_id} (lifecycle/stop.py over redis) ---
    from .lifecycle.stop_router import InMemoryCommandPublisher, build_stop_router

    if command_publisher is None:
        command_publisher = InMemoryCommandPublisher()
    app.state.command_publisher = command_publisher
    # The stop router also gets the runtime client so a stop can directly tear down a still-booting bot's
    # workload (the leave command alone is fire-and-forget — a booting bot may never receive it → orphan).
    app.include_router(build_stop_router(meeting_repo, command_publisher, runtime))

    # --- collector: transcripts + meetings + ws-authorize (api.v1) ---
    if transcript_store is None:
        transcript_store = _collector_fakes().InMemoryTranscriptStore()
    app.include_router(_build_collector_router(transcript_store, redis))

    # --- recordings: chunk upload + finalize → meeting.data JSONB (recording.v1) ---
    if recording_repo is None:
        recording_repo = _recordings_fakes().InMemoryRecordingRepo()
    if storage is None:
        storage = _recordings_fakes().InMemoryStorage()
    app.include_router(_recordings.build_router(recording_repo, storage, token_secret=token_secret))

    return app


# ── lifecycle mount (the receiver's callback route, on the shared app) ───────────────────────────


def _mount_lifecycle(
    app: FastAPI,
    sink: LifecycleSink,
    meeting_repo: "_bot_spawn.MeetingRepo",
    webhook_sink: "object" = None,
    redis: "object" = None,
    transcript_finalizer: "object" = None,
) -> None:
    """Register the lifecycle.v1 callback route on the unified app (the lifecycle receiver's
    ``/bots/internal/callback/lifecycle`` handler, sharing the app's TraceMiddleware).

    P3a — each FSM advance emits the sealed ``meeting.status_change`` webhook.v1 envelope and
    records the full diagnostics (``status_transition[]`` + forensics in ``rec.data``). The
    receiver is a bot callback → ``transition_source=bot_callback``. Each advance is ALSO persisted
    to the DB meeting row via ``meeting_repo`` (durable + queryable status, not only the in-process
    store). Also mounts ``POST /runtime/callback`` so the runtime kernel's workload callbacks ACK
    (no 404-retry).

    Before applying an event the callback REHYDRATES the in-memory FSM record from the DB meeting
    status, so the FSM survives a process restart (the in-process store starts empty) and a terminal
    callback reconciles against the durable status. After a persisted advance it PUBLISHES a ws.v1
    ``BotStatus`` frame to ``bm:meeting:{id}:status`` for the gateway ``/ws`` to forward to clients.
    """
    import jsonschema

    from .lifecycle.machine import IllegalTransition, TransitionSource
    from .lifecycle.receiver import conforms
    from .lifecycle.webhook import build_status_change_envelope, build_typed_envelope
    from .obs import log_event
    from .webhooks import clean_meeting_data

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    def _meeting_projection_from_row(row: dict) -> dict:
        """The parent's `_build_meeting_event_data` shape (webhooks.py) from a meeting row dict —
        the meeting block the typed webhooks carry (golden Envelope.meeting-completed.json).
        completion_reason/failure_stage are hoisted to top level; internal data keys stripped."""
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "platform": row.get("platform"),
            "native_meeting_id": row.get("native_meeting_id"),
            "constructed_meeting_url": row.get("constructed_meeting_url"),
            "status": row.get("status"),
            "completion_reason": data.get("completion_reason"),
            "failure_stage": data.get("failure_stage"),
            "start_time": _iso(row.get("start_time")),
            "end_time": _iso(row.get("end_time")),
            "data": clean_meeting_data(data),
            "created_at": _iso(row.get("created_at")),
            "updated_at": _iso(row.get("updated_at")),
        }

    app.state.status_change_webhooks = []
    app.state.typed_webhooks = []

    @app.post("/bots/internal/callback/lifecycle")
    async def lifecycle_callback(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            conforms(body, "LifecycleEvent")
        except jsonschema.ValidationError as e:
            log_event(
                "lifecycle_event_rejected", audience="system", level="warning",
                span="lifecycle.callback",
                fields={"reason": "schema_violation", "detail": e.message},
            )
            return JSONResponse(
                status_code=422,
                content={"status": "error", "detail": f"lifecycle.v1 schema violation: {e.message}"},
            )
        # LIFECYCLE-409 fix: rehydrate the in-memory FSM record from the DB's CURRENT status before
        # applying the event. The in-memory MeetingStore is non-durable — after a meeting-api restart
        # it is empty, so a bot's terminal `completed` event would land on a fresh status=None record
        # → can_transition(None, COMPLETED) is False → IllegalTransition → 409, the bot retries 3x,
        # all 409, and the meeting stays stuck `active`. Seeding the record from the persisted status
        # first makes active/stopping → completed a legal transition again. Best-effort: a DB hiccup
        # must never fail the callback (we fall back to the in-process record as-is).
        connection_id = body.get("connection_id")
        if connection_id:
            existing = sink.store.get(connection_id)
            if existing is None or existing.status is None:
                try:
                    persisted = await meeting_repo.get_status_by_session(session_uid=connection_id)
                except Exception as e:  # noqa: BLE001 — rehydration is best-effort
                    persisted = None
                    log_event("lifecycle_rehydrate_failed", audience="system", level="warning",
                              span="lifecycle.callback", fields={"error": str(e)})
                if persisted:
                    sink.store.rehydrate(connection_id, persisted)
        try:
            change = sink.apply_change(body, transition_source=TransitionSource.BOT_CALLBACK)
        except IllegalTransition as e:
            return JSONResponse(
                status_code=409,
                content={
                    "status": "error", "detail": str(e),
                    "connection_id": e.connection_id,
                    "from": e.frm.value if e.frm is not None else None,
                    "to": e.to.value,
                },
            )
        rec = change.record
        # Build + record the status_change envelope only on a REAL advance — an idempotent replay
        # (change.no_op, e.g. the bot's 3x terminal retry) must NOT double-count it. The persist, the
        # webhook deliver, and the ws publish below are already no_op-gated (they hang off meeting_row,
        # set only on a real persist), so end-user delivery is exactly-once; this keeps the in-process
        # envelope log honest too.
        envelope = None
        if not change.no_op:
            envelope = build_status_change_envelope(change)
            app.state.status_change_webhooks.append(envelope)
        # Persist the FSM advance to the DB meeting row → durable + queryable (GET /meetings reflects
        # it, survives a restart), not only the in-process MeetingStore. Best-effort: a DB hiccup must
        # never fail the bot's lifecycle callback (the in-process FSM + webhook already advanced).
        # On an idempotent replay (change.no_op) the FSM did not actually advance — skip the
        # re-persist + re-deliver so a redelivered terminal does not fire a duplicate webhook /
        # publish. We still return 200 (handled below) — the redelivery is acknowledged as a no-op.
        meeting_row = None
        if rec.status is not None and not change.no_op:
            try:
                meeting_row = await meeting_repo.update_meeting_status(
                    session_uid=rec.connection_id,
                    status=rec.status.value,
                    completion_reason=rec.completion_reason.value if rec.completion_reason else None,
                    failure_stage=rec.failure_stage.value if rec.failure_stage else None,
                    data=rec.data if isinstance(rec.data, dict) else None,
                )
            except Exception as e:  # noqa: BLE001 — persistence is best-effort
                log_event("lifecycle_persist_failed", audience="system", level="warning",
                          span="lifecycle.callback", fields={"error": str(e)})
        # COMPLETION FINALIZATION — the moment the FSM lands on a terminal status, flush the
        # meeting's remaining live redis segments to the durable store (threshold 0: the mutable
        # tail included, no more updates are coming) and persist the processed doc into
        # meeting.data, via the injected finalizer (prod: collector/db_writer.finalize_meeting).
        # This guarantees a completed meeting's transcript is durable even if the periodic
        # db-writer never gets another tick (crash/restart right after completion). Best-effort:
        # the periodic loop retries anything this misses; never fail the bot's callback.
        if (
            transcript_finalizer is not None
            and not change.no_op
            and rec.status is not None
            and rec.status.value in ("completed", "failed")
            and isinstance(meeting_row, dict)
            and meeting_row.get("id") is not None
        ):
            try:
                await transcript_finalizer(meeting_row["id"])
            except Exception as e:  # noqa: BLE001 — the db-writer loop is the retry path
                log_event("transcript_finalize_failed", audience="system", level="warning",
                          span="lifecycle.callback",
                          fields={"meeting_id": meeting_row.get("id"), "error": str(e)})
        # Build the TYPED event the transition maps to (meeting.started on active,
        # meeting.completed with the post-meeting envelope on completion, bot.failed on terminal
        # failure) — additive alongside meeting.status_change, never instead of it. Built AFTER the
        # persist so the meeting block is the durable row projection (the parent's
        # _build_meeting_event_data shape) when the row is known; the FSM-record fallback otherwise.
        typed_envelope = None
        if not change.no_op:
            typed_envelope = build_typed_envelope(
                change,
                meeting=_meeting_projection_from_row(meeting_row)
                if isinstance(meeting_row, dict) else None,
            )
            if typed_envelope is not None:
                app.state.typed_webhooks.append(typed_envelope)
        # Deliver the sealed webhook.v1 envelopes (meeting.status_change + the typed event, if any)
        # to the user's configured endpoint (per-user config rides on meeting.data — set at spawn
        # from identity via the gateway; NO users-table read). The sink's per-user event filter
        # (webhooks/delivery.py) suppresses unsubscribed event types before any HTTP.
        # Best-effort: a delivery hiccup must never fail the bot's lifecycle callback (P3a).
        if webhook_sink is not None and isinstance(meeting_row, dict):
            data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
            url = data.get("webhook_url")
            if url:
                for env in (envelope, typed_envelope):
                    if env is None:
                        continue
                    try:
                        await webhook_sink.deliver(
                            url, env, data.get("webhook_secret"),
                            events_config=data.get("webhook_events"),
                            label=f"meeting:{meeting_row.get('id')}",
                        )
                    except Exception as e:  # noqa: BLE001 — delivery is best-effort
                        log_event("webhook_deliver_failed", audience="system", level="warning",
                                  span="lifecycle.callback", fields={"error": str(e)})
        # Publish each persisted FSM advance to bm:meeting:{id}:status in the canonical 0.10.6 WS
        # contract shape (the source of truth; api-gateway forwards the redis payload verbatim):
        #   {type:"meeting.status", meeting:{id,platform,native_id}, payload:{status}, user_id, ts}
        # `status` is the raw BotStatus value (e.g. 'needs_help'); clients translate to their own
        # vocabulary on THEIR side (the core emits the contract, never a client's naming). Skipped on
        # a no-op advance (idempotent replay) / unknown session. Best-effort: never fail the callback.
        if redis is not None and not change.no_op and isinstance(meeting_row, dict) and rec.status is not None:
            meeting_id = meeting_row.get("id")
            if meeting_id is not None:
                import json as _json
                from datetime import datetime, timezone

                frame = {
                    "type": "meeting.status",
                    "meeting": {
                        "id": meeting_id,
                        "platform": meeting_row.get("platform"),
                        "native_id": meeting_row.get("native_meeting_id"),
                    },
                    "payload": {"status": rec.status.value},
                    "user_id": meeting_row.get("user_id"),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await redis.publish(f"bm:meeting:{meeting_id}:status", _json.dumps(frame))
                except Exception as e:  # noqa: BLE001 — publish is best-effort
                    log_event("ws_status_publish_failed", audience="system", level="warning",
                              span="lifecycle.callback", fields={"error": str(e)})
                # ALSO publish the FLAT frame to the USER-scoped channel u:{user_id}:meetings so the
                # terminal's list surface gets every bot-FSM transition over WS (superset of bm:; it
                # also carries the pre-FSM idle/scheduled states). KEEP bm: above for the open-meeting
                # tab. Best-effort: never fail the lifecycle callback.
                user_id = meeting_row.get("user_id")
                if user_id is not None:
                    user_frame = {
                        "type": "meeting.status",
                        "meeting_id": meeting_id,
                        "native": meeting_row.get("native_meeting_id"),
                        "status": rec.status.value,
                        "when": frame["ts"],
                    }
                    try:
                        await redis.publish(
                            f"u:{user_id}:meetings", _json.dumps(user_frame)
                        )
                    except Exception as e:  # noqa: BLE001 — publish is best-effort
                        log_event("user_meeting_status_publish_failed", audience="system",
                                  level="warning", span="lifecycle.callback",
                                  fields={"error": str(e)})
        log_event(
            "meeting_lifecycle_advanced", audience="user", span="lifecycle.callback",
            meeting_id=rec.connection_id,
            fields={"meeting_status": rec.status.value if rec.status else None},
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "accepted",
                "connection_id": rec.connection_id,
                "meeting_status": rec.status.value if rec.status else None,
                "completion_reason": rec.completion_reason.value if rec.completion_reason else None,
                "failure_stage": rec.failure_stage.value if rec.failure_stage else None,
                "transition_source": change.transition_source.value,
                "status_transition": rec.status_transition,
                "data": rec.data,
            },
        )

    @app.post("/runtime/callback")
    async def runtime_callback(request: Request) -> JSONResponse:
        """ACK the runtime kernel's workload-level callback (state/terminal events). The bot's own
        ``lifecycle.v1`` callback is the meeting-status source of truth for a STARTED bot; this route
        also drives a synthetic ``failed`` (CC5) when a workload reaches a TERMINAL state while its
        meeting is still PRE-ACTIVE — i.e. the bot never started/reported and never will, so the meeting
        would otherwise hang ``requested``/``joining`` forever."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        workload_id = body.get("workloadId") or body.get("workload_id")
        state = body.get("state")
        log_event(
            "runtime_callback", audience="system", span="runtime.callback",
            fields={"workload_id": workload_id, "state": state},
        )
        # CC5 — never-started workload → meeting failed. Reuse the bot's OWN lifecycle callback (POST to
        # self) so the FSM/persist/webhook/ws path fires identically; the decision is best-effort + only
        # fires for a PRE-ACTIVE meeting (a normal teardown destroys the workload AFTER the meeting is
        # already terminal → no-op). Imported lazily to keep the prod import path lean.
        try:
            import logging as _logging
            import os as _os

            from .lifecycle.reconcile import synthesize_failed_for_dead_workload

            async def _drive_failed(event: dict):
                import httpx

                port = int(_os.getenv("PORT", "8080"))
                url = f"http://127.0.0.1:{port}/bots/internal/callback/lifecycle"
                headers = {"content-type": "application/json"}
                secret = _os.getenv("INTERNAL_API_SECRET")
                if secret:
                    headers["x-internal-secret"] = secret
                async with httpx.AsyncClient(timeout=10.0) as client:
                    return (await client.post(url, json=event, headers=headers)).status_code

            await synthesize_failed_for_dead_workload(
                meeting_repo, workload_id, state, _drive_failed,
                log=_logging.getLogger("meeting_api.runtime.callback"),
            )
        except Exception as e:  # noqa: BLE001 — the runtime ACK must never fail on the CC5 backstop
            log_event("runtime_callback_cc5_error", audience="system", level="warning",
                      span="runtime.callback", fields={"error": str(e)})
        return JSONResponse(status_code=200, content={"status": "accepted"})


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
