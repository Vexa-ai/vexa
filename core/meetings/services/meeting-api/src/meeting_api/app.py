"""``create_app(...) -> FastAPI`` — the ONE uvicorn-able meeting-api modular monolith (P2).

This is the unified meeting-api: ONE FastAPI app composed of front-doored modules, each a
sub-package of ``meeting_api`` mounted here (the v0.12 analog of the parent ``main.py``'s flat
``app.include_router(...)`` list, but each module is an isolated brick behind a port-seam):

  * **lifecycle** — the bot lifecycle callback receiver + meeting-state FSM (lifecycle.v1):
    POST ``/bots/internal/callback/lifecycle``.
  * **bot_spawn** — POST ``/bots``: build the invocation.v1 invocation + mint the MeetingToken +
    spawn the meeting-bot over runtime.v1, eager-creating the MeetingSession on spawn.
  * **collector** — the folded-in transcript backend (was the standalone transcription-collector):
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

    # --- shared liveness probe (gate:health): the unified process is up. No auth, no I/O. ---
    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "meeting-api"}

    # --- bot_spawn ports (resolved FIRST: the meeting_repo is also the lifecycle-persistence target) ---
    if meeting_repo is None:
        meeting_repo = _bot_spawn_fakes().InMemoryMeetingRepo()
    if runtime is None:
        runtime = _bot_spawn_fakes().FakeRuntimeClient()

    # --- lifecycle: bot lifecycle callbacks + FSM (lifecycle.v1), PERSISTED to the meeting row ---
    sink = LifecycleSink(store=meeting_store if meeting_store is not None else MeetingStore())
    app.state.lifecycle_sink = sink
    app.state.lifecycle_store = sink.store
    _mount_lifecycle(app, sink, meeting_repo)

    # --- bot_spawn: POST /bots (invocation.v1 + runtime.v1) ---
    app.include_router(_bot_spawn.build_router(meeting_repo, runtime))

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


def _mount_lifecycle(app: FastAPI, sink: LifecycleSink, meeting_repo: "_bot_spawn.MeetingRepo") -> None:
    """Register the lifecycle.v1 callback route on the unified app (the lifecycle receiver's
    ``/bots/internal/callback/lifecycle`` handler, sharing the app's TraceMiddleware).

    P3a — each FSM advance emits the sealed ``meeting.status_change`` webhook.v1 envelope and
    records the full diagnostics (``status_transition[]`` + forensics in ``rec.data``). The
    receiver is a bot callback → ``transition_source=bot_callback``. Each advance is ALSO persisted
    to the DB meeting row via ``meeting_repo`` (durable + queryable status, not only the in-process
    store). Also mounts ``POST /runtime/callback`` so the runtime kernel's workload callbacks ACK
    (no 404-retry).
    """
    import jsonschema

    from .lifecycle.machine import IllegalTransition, TransitionSource
    from .lifecycle.receiver import conforms
    from .lifecycle.webhook import build_status_change_envelope
    from .obs import log_event

    app.state.status_change_webhooks = []

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
        envelope = build_status_change_envelope(change)
        app.state.status_change_webhooks.append(envelope)
        # Persist the FSM advance to the DB meeting row → durable + queryable (GET /meetings reflects
        # it, survives a restart), not only the in-process MeetingStore. Best-effort: a DB hiccup must
        # never fail the bot's lifecycle callback (the in-process FSM + webhook already advanced).
        if rec.status is not None:
            try:
                await meeting_repo.update_meeting_status(
                    session_uid=rec.connection_id,
                    status=rec.status.value,
                    completion_reason=rec.completion_reason.value if rec.completion_reason else None,
                    failure_stage=rec.failure_stage.value if rec.failure_stage else None,
                    data=rec.data if isinstance(rec.data, dict) else None,
                )
            except Exception as e:  # noqa: BLE001 — persistence is best-effort
                log_event("lifecycle_persist_failed", audience="system", level="warning",
                          span="lifecycle.callback", fields={"error": str(e)})
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
        ``lifecycle.v1`` callback is the meeting-status source of truth (persisted above); this route
        exists so the kernel's callback does not 404-retry. (Mapping a never-started workload →
        meeting ``failed`` is a follow-up; the started-bot path is fully covered by the bot callback.)"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        log_event(
            "runtime_callback", audience="system", span="runtime.callback",
            fields={"workload_id": body.get("workloadId") or body.get("workload_id"),
                    "state": body.get("state")},
        )
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
