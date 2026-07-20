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
from .zaki_read import build_router as _build_zaki_read_router


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
    # calendar-sync user edges (async callables from the composition root; None → routes 503)
    calendar_sync_now: Optional["object"] = None,
    calendar_sync_status: Optional["object"] = None,
    # Internal ZAKI Minutes read plane. No token means default-off/fail-closed.
    zaki_read_token: Optional[str] = None,
    zaki_read_now: Optional["object"] = None,
    # Sealed Hub-BFF Minutes control plane. ``None`` means no control routes are mounted.
    zaki_control_store: Optional["object"] = None,
    zaki_control_config: Optional["object"] = None,
    zaki_control_retention_repo: Optional["object"] = None,
    zaki_control_retention_storage: Optional["object"] = None,
    zaki_control_callback: Optional["object"] = None,
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
    _mount_lifecycle(
        app, sink, meeting_repo, webhook_sink, redis, transcript_finalizer,
        zaki_control_callback,
    )

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
    app.include_router(_build_collector_router(transcript_store, redis,
                                            calendar_sync_now=calendar_sync_now,
                                            calendar_sync_status=calendar_sync_status))
    app.include_router(_build_zaki_read_router(
        transcript_store,
        token=zaki_read_token,
        **({"now": zaki_read_now} if zaki_read_now is not None else {}),
    ))

    # --- recordings: chunk upload + finalize → meeting.data JSONB (recording.v1) ---
    if recording_repo is None:
        recording_repo = _recordings_fakes().InMemoryRecordingRepo()
    if storage is None:
        storage = _recordings_fakes().InMemoryStorage()
    app.include_router(_recordings.build_router(recording_repo, storage, token_secret=token_secret))

    # The private control router is a deliberate activation seam.  It is not mounted in default
    # deployments, and it requires all real collaborators once composition enables it.
    if zaki_control_config is not None:
        if any(value is None for value in (
            zaki_control_store, zaki_control_retention_repo, zaki_control_retention_storage,
            zaki_control_callback,
        )):
            raise ValueError("ZAKI Minutes control requires durable policy, retention, and callback collaborators")
        from .zaki_control import build_router as _build_zaki_control_router

        app.include_router(_build_zaki_control_router(
            store=zaki_control_store,
            config=zaki_control_config,
            meeting_repo=meeting_repo,
            runtime=runtime,
            command_publisher=command_publisher,
            retention_repo=zaki_control_retention_repo,
            retention_storage=zaki_control_retention_storage,
            callback_dispatcher=zaki_control_callback,
        ))
        app.state.zaki_control_store = zaki_control_store
        app.state.zaki_control_callback = zaki_control_callback
        # Production flips this false while the lifespan creates/verifies the durable control
        # tables.  The Hub startup gate uses this deliberately narrow, private endpoint instead of
        # treating generic `/health` as evidence that the control plane is actually mounted.
        app.state.zaki_control_ready = True

        @app.get("/api/zaki/control/v1/ready")
        async def zaki_control_ready():
            if not getattr(app.state, "zaki_control_ready", False):
                return JSONResponse(
                    status_code=503,
                    content={"api_version": "zaki-control.v1", "state": "starting"},
                    headers={"Cache-Control": "no-store"},
                )
            # `operator_enabled` reports the operator gate truthfully rather than asserting it.
            # The infra startup gate greps for `"operator_enabled":true`, so an operator-disabled
            # engine correctly fails to satisfy the Hub rollout contract instead of faking it.
            return JSONResponse(
                status_code=200,
                content={
                    "api_version": "zaki-control.v1",
                    "state": "ready",
                    "operator_enabled": bool(zaki_control_config.operator_enabled),
                    "max_capture_seconds": int(zaki_control_config.max_capture_seconds),
                },
                headers={"Cache-Control": "no-store"},
            )

    return app


# ── lifecycle mount (the receiver's callback route, on the shared app) ───────────────────────────


def _mount_lifecycle(
    app: FastAPI,
    sink: LifecycleSink,
    meeting_repo: "_bot_spawn.MeetingRepo",
    webhook_sink: "object" = None,
    redis: "object" = None,
    transcript_finalizer: "object" = None,
    zaki_control_callback: "object" = None,
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
    from copy import deepcopy

    import jsonschema

    from .lifecycle.machine import IllegalTransition, TransitionSource
    from .lifecycle.receiver import conforms
    from .lifecycle.webhook import build_status_change_envelope, build_typed_envelope
    from .meeting_writes import capture_is_withdrawn
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

    async def _apply_lifecycle_event(
        body: dict,
        *,
        transition_source: "TransitionSource" = TransitionSource.BOT_CALLBACK,
        force_terminal_on_destroy: bool = False,
    ) -> tuple[int, dict]:
        """Apply ONE lifecycle.v1 event to the FSM + run every side effect (persist, finalize,
        webhook deliver, ws publish, copilot reap), returning ``(status_code, content)``.

        This is the SINGLE in-process entry the FSM advance flows through — the HTTP endpoint
        ``POST /bots/internal/callback/lifecycle`` (the bot's own callback) is a thin wrapper around
        it, and the runtime-callback synthetic-terminal path calls it DIRECTLY (no HTTP self-POST).
        The prior implementation POSTed to ``http://127.0.0.1:PORT/…`` to re-enter this logic; that
        loopback round-trip was fragile (a rehydration race made the synthetic terminal 409, and
        under any harness that cannot reach the loopback it silently dropped) — the direct in-process
        call removes the network hop entirely, so the synthetic terminal advances the SAME FSM
        instance deterministically. ``force_terminal_on_destroy`` rides through to the sink so a
        runtime-confirmed destroy can force the terminal edge from a stale non-terminal state."""
        try:
            conforms(body, "LifecycleEvent")
        except jsonschema.ValidationError as e:
            log_event(
                "lifecycle_event_rejected", audience="system", level="warning",
                span="lifecycle.callback",
                fields={"reason": "schema_violation", "detail": e.message},
            )
            return (
                422,
                {"status": "error", "detail": f"lifecycle.v1 schema violation: {e.message}"},
            )
        # LIFECYCLE-409 fix: rehydrate the in-memory FSM record from the DB's CURRENT status before
        # applying the event. The in-memory MeetingStore is non-durable — after a meeting-api restart
        # it is empty, so a bot's terminal `completed` event would land on a fresh status=None record
        # → can_transition(None, COMPLETED) is False → IllegalTransition → 409, the bot retries 3x,
        # all 409, and the meeting stays stuck `active`. Seeding the record from the persisted status
        # first makes active/stopping → completed a legal transition again. Best-effort: a DB hiccup
        # must never fail the callback (we fall back to the in-process record as-is).
        connection_id = body.get("connection_id")
        persisted = None
        if connection_id:
            existing = sink.store.get(connection_id)
            needs_rehydrate = existing is None or existing.status is None
            needs_stop_reconcile = body.get("status") in ("completed", "failed")
            if needs_rehydrate or needs_stop_reconcile:
                try:
                    persisted = await meeting_repo.get_status_by_session(session_uid=connection_id)
                except Exception as e:  # noqa: BLE001 — rehydration is best-effort
                    persisted = None
                    log_event("lifecycle_rehydrate_failed", audience="system", level="warning",
                              span="lifecycle.callback", fields={"error": str(e)})
                if needs_rehydrate and persisted:
                    sink.store.rehydrate(connection_id, persisted)
        accepted_record = deepcopy(sink.store.get(connection_id)) if connection_id else None
        try:
            change = sink.apply_change(
                body,
                transition_source=transition_source,
                force_terminal_on_destroy=force_terminal_on_destroy,
                force_terminal_after_stop=bool(
                    (accepted_record is not None and accepted_record.stop_requested)
                    or persisted == "stopping"
                ),
            )
        except IllegalTransition as e:
            return (
                409,
                {
                    "status": "error", "detail": str(e),
                    "connection_id": e.connection_id,
                    "from": e.frm.value if e.frm is not None else None,
                    "to": e.to.value,
                },
            )
        rec = change.record
        # Build + record the status_change envelope only after persistence confirms that the advance
        # is user-visible. Besides idempotent replays, a late non-terminal callback after durable
        # consent withdrawal is suppressed: it must not resurrect an active state in HTTP, WS, or
        # webhook projections.
        envelope = None
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
        meeting_data = (
            meeting_row.get("data")
            if isinstance(meeting_row, dict) and isinstance(meeting_row.get("data"), dict)
            else {}
        )
        suppressed_withdrawn_advance = (
            not change.no_op
            and rec.status is not None
            and rec.status.value not in ("completed", "failed")
            and isinstance(meeting_row, dict)
            and capture_is_withdrawn(meeting_data)
            and meeting_row.get("status") != rec.status.value
        )
        if suppressed_withdrawn_advance and accepted_record is not None:
            accepted_record.stop_requested = True
            sink.store.replace(accepted_record)
        visible_change = not change.no_op and not suppressed_withdrawn_advance
        if visible_change:
            envelope = build_status_change_envelope(change)
            app.state.status_change_webhooks.append(envelope)
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
        if visible_change:
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
        # Queue the sealed Hub callback only after the durable meeting update.  Best-effort, like
        # every other side effect in this function: failing the lifecycle callback here would skip
        # the WS status publish and the copilot reap below, both of which are documented as never
        # failing this callback — and it would not even buy a retry. The FSM record and the meeting
        # row are already advanced by this point, so a redelivery of the same event returns
        # `no_op=True` from `apply_change`, `visible_change` is False, and this block is skipped
        # entirely. The real backstop for a missed transition is the `reconcile_once` sweep, which
        # rebuilds the outbox from the durable meeting rows.
        if (
            zaki_control_callback is not None
            and visible_change
            and isinstance(meeting_row, dict)
            and rec.status is not None
        ):
            try:
                await zaki_control_callback.record_lifecycle(
                    meeting_row, state=rec.status.value
                )
            except Exception as e:  # noqa: BLE001 — reconciliation owns recovery, not the bot ACK
                log_event("zaki_control_callback_queue_failed", audience="system", level="warning",
                          span="lifecycle.callback", fields={"error": str(e)})
        # Publish each persisted FSM advance to bm:meeting:{id}:status in the canonical 0.10.6 WS
        # contract shape (the source of truth; api-gateway forwards the redis payload verbatim):
        #   {type:"meeting.status", meeting:{id,platform,native_id}, payload:{status}, user_id, ts}
        # `status` is the raw BotStatus value (e.g. 'needs_help'); clients translate to their own
        # vocabulary on THEIR side (the core emits the contract, never a client's naming). Skipped on
        # a no-op advance (idempotent replay) / unknown session. Best-effort: never fail the callback.
        if redis is not None and visible_change and isinstance(meeting_row, dict) and rec.status is not None:
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
        # COPILOT REAP (Bug 3): the moment a meeting lands TERMINAL, emit the `session_end` marker onto
        # the meeting copilot transcript feed — the EXACT stream the meeting copilot worker
        # (agent worker/meeting.py, via VEXA_TRANSCRIPT_STREAM) blocks on. The worker reaps immediately
        # on that marker (exit 0 → container reaped), instead of sitting idle for its
        # VEXA_IDLE_TIMEOUT_SEC (default 4h) when the bot never emitted its own `session_end` — e.g. it
        # was SIGKILLed, or stopped in the waiting room (Bug 2) before it could. Idempotent: a redundant
        # session_end (the bot already sent one via the collector) just reasserts the reap. Best-effort;
        # never fails the lifecycle callback.
        #
        # KEYING (P0 fix/transcript-cross-tenant-leak, now merged): the carrier is ROW-scoped
        # `tc:meeting:{meeting_row_id}` — the numeric meetings-domain ROW id, NOT the native id (which
        # collides across tenants/rows and is never a data key post-P0). The collector
        # (collector/ingest.py `_transcript_stream`) writes its session_end on the same row key and the
        # worker tails the row key (agent dispatch.py sets VEXA_TRANSCRIPT_STREAM=tc:meeting:{row_id}),
        # so this lifecycle reap must key by the row id to land on the live stream the worker blocks on.
        if (
            redis is not None
            and not change.no_op
            and rec.status is not None
            and rec.status.value in ("completed", "failed")
            and isinstance(meeting_row, dict)
            and hasattr(redis, "xadd")
        ):
            meeting_row_id = meeting_row.get("id")
            native = meeting_row.get("native_meeting_id") or rec.connection_id
            if meeting_row_id is not None:
                try:
                    await redis.xadd(
                        f"tc:meeting:{meeting_row_id}",
                        {"type": "session_end", "uid": str(native or meeting_row_id)},
                    )
                    log_event(
                        "meeting_copilot_reap_signalled", audience="system", span="lifecycle.callback",
                        meeting_id=rec.connection_id,
                        fields={"meeting_row_id": meeting_row_id, "native": native,
                                "meeting_status": rec.status.value},
                    )
                except Exception as e:  # noqa: BLE001 — the worker's idle timeout is the backstop
                    log_event("meeting_copilot_reap_failed", audience="system", level="warning",
                              span="lifecycle.callback",
                              fields={"meeting_row_id": meeting_row_id, "error": str(e)})
        reported_status = (
            meeting_row.get("status")
            if suppressed_withdrawn_advance and isinstance(meeting_row, dict)
            else (rec.status.value if rec.status else None)
        )
        reported_data = meeting_data if suppressed_withdrawn_advance else rec.data
        reported_transitions = (
            meeting_data.get("status_transition", [])
            if suppressed_withdrawn_advance
            else rec.status_transition
        )
        log_event(
            "meeting_lifecycle_advanced", audience="user", span="lifecycle.callback",
            meeting_id=rec.connection_id,
            fields={"meeting_status": reported_status},
        )
        return (
            200,
            {
                "status": "accepted",
                "connection_id": rec.connection_id,
                "meeting_status": reported_status,
                "completion_reason": rec.completion_reason.value if rec.completion_reason else None,
                "failure_stage": rec.failure_stage.value if rec.failure_stage else None,
                "transition_source": change.transition_source.value,
                "status_transition": reported_transitions,
                "data": reported_data,
            },
        )

    # Expose the in-process entry so the runtime-callback synthetic-terminal path can advance the FSM
    # DIRECTLY (no HTTP self-POST to 127.0.0.1:PORT). Same instance, same store, same side effects.
    app.state.apply_lifecycle_event = _apply_lifecycle_event

    @app.post("/bots/internal/callback/lifecycle")
    async def lifecycle_callback(request: Request) -> JSONResponse:
        body = await request.json()
        status_code, content = await _apply_lifecycle_event(
            body, transition_source=TransitionSource.BOT_CALLBACK
        )
        return JSONResponse(status_code=status_code, content=content)

    @app.post("/runtime/callback")
    async def runtime_callback(request: Request) -> JSONResponse:
        """ACK the runtime kernel's workload-level callback (state/terminal events). The bot's own
        ``lifecycle.v1`` callback is the meeting-status source of truth for a STARTED bot; this route
        ALSO consumes a runtime-confirmed TERMINAL workload state as evidence the run is over, driving a
        synthetic terminal through the SAME in-process lifecycle logic (no HTTP self-POST):

          * PRE-ACTIVE meeting → ``failed`` (CC5): the bot never started/reported and never will, so the
            meeting would otherwise hang ``requested``/``joining`` forever.
          * WAS-ACTIVE meeting (``stopping``/``active``/``needs_help``) → ``completed``: the bot reached
            the meeting but its workload is now runtime-confirmed gone WITHOUT its own terminal callback
            (e.g. SIGKILLed at teardown before it could POST ``completed``, or killed in the waiting room
            on a stop). Without this the meeting stays ``stopping`` and the stop-reconcile sweep re-DELETEs
            (now 404) every 15s FOREVER — the reaper loop. The confirmed destroy IS the terminal evidence
            (#50's principle: real evidence, not a bare 404) → complete it and stop the loop.

        THE FIX (live 409): the synthetic terminal is applied by calling the in-process lifecycle entry
        (``app.state.apply_lifecycle_event``) DIRECTLY with ``transition_source=RUNTIME_DESTROY`` and
        ``force_terminal_on_destroy=True`` — NOT an httpx POST to ``127.0.0.1:PORT``. The old self-POST
        409'd whenever the in-process FSM record was a stale non-terminal state the DB had already moved
        past (e.g. store still ``joining`` while the DB user-stop set ``stopping`` — ``joining →
        completed`` is illegal for a bot-driven edge). The direct in-process call advances the SAME FSM
        instance, and the runtime-destroy source forces the terminal edge on real teardown evidence, so
        the meeting reaches terminal, the reaper stops, and the copilot ``session_end`` reap fires."""
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
        # Consume a runtime-confirmed TERMINAL workload as evidence (pre-active → failed / was-active →
        # completed). Drive it through the SAME in-process lifecycle logic (FSM/persist/webhook/ws/reap
        # all fire identically) — best-effort; a non-terminal state or an already-terminal meeting is a
        # no-op. Imported lazily to keep the prod import path lean.
        try:
            import logging as _logging

            from .lifecycle.machine import TransitionSource as _TS
            from .lifecycle.reconcile import synthesize_terminal_for_dead_workload

            async def _drive_terminal(event: dict):
                # In-process — no network hop. The runtime-destroy source forces the terminal edge past
                # a stale non-terminal FSM record; returns the HTTP-equivalent status code for the log.
                status_code, _content = await _apply_lifecycle_event(
                    event,
                    transition_source=_TS.RUNTIME_DESTROY,
                    force_terminal_on_destroy=True,
                )
                return status_code

            await synthesize_terminal_for_dead_workload(
                meeting_repo, workload_id, state, _drive_terminal,
                log=_logging.getLogger("meeting_api.runtime.callback"),
            )
        except Exception as e:  # noqa: BLE001 — the runtime ACK must never fail on the terminal backstop
            log_event("runtime_callback_terminal_error", audience="system", level="warning",
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
