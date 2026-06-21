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

    # --- lifecycle: bot lifecycle callbacks + FSM (lifecycle.v1) ---
    sink = LifecycleSink(store=meeting_store if meeting_store is not None else MeetingStore())
    app.state.lifecycle_sink = sink
    app.state.lifecycle_store = sink.store
    _mount_lifecycle(app, sink)

    # --- bot_spawn: POST /bots (invocation.v1 + runtime.v1) ---
    if meeting_repo is None:
        meeting_repo = _bot_spawn_fakes().InMemoryMeetingRepo()
    if runtime is None:
        runtime = _bot_spawn_fakes().FakeRuntimeClient()
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


def _mount_lifecycle(app: FastAPI, sink: LifecycleSink) -> None:
    """Register the lifecycle.v1 callback route on the unified app (the lifecycle receiver's
    ``/bots/internal/callback/lifecycle`` handler, sharing the app's TraceMiddleware)."""
    import jsonschema

    from .lifecycle.machine import IllegalTransition
    from .lifecycle.receiver import conforms
    from .obs import log_event

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
            rec = sink.apply(body)
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
            },
        )


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
