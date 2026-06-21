# meeting_api — the modular-monolith package (public surface = `__init__`)

The cloud control-plane, assembled as ONE uvicorn-able app. Public surface is
`meeting_api/__init__.py`: **`create_app(...)`** (the unified app factory) + `build_recording_master`
+ the front-doored sub-package modules. `create_app` composes the modules below onto one FastAPI app
(`app.py`) — the v0.12 unification (P2) of the parent `main.py`'s `include_router` list, each module
an isolated brick behind a port-seam.

| Module | Concern | HTTP surface (on the unified app) |
|---|---|---|
| `app.py` | `create_app(...)` — composes the modules onto ONE app; the shared `/health`. | `GET /health` |
| `lifecycle/` | **O-MTG-1** — the lifecycle.v1 receiver + meeting-state FSM. | `POST /bots/internal/callback/lifecycle` |
| `bot_spawn/` | `POST /bots` — build the invocation.v1 invocation + mint the MeetingToken + spawn the meeting-bot over runtime.v1, eager-creating the MeetingSession. | `POST /bots` |
| `collector/` | the **folded-in** transcript backend (was the standalone transcription-collector): api.v1 reads + the `/ws` authorizer + the segments consumer. | `GET /transcripts/…`, `GET /meetings`, `POST /ws/authorize-subscribe` |
| `recordings/` | chunk upload + finalize → master in `meeting.data` JSONB (recording.v1). | `POST /internal/recordings/upload`, `GET /recordings`, `GET /recordings/{id}/master` |
| `sessions/` | the `MeetingSession` model + the shared SQLAlchemy mirror (Meeting/Transcription/MeetingSession) every module binds. | — |
| `recording_codec.py` | the pure master codec — `build_recording_master` (front door) → WebM byte-concat / WAV RIFF header-merge. The Python twin of `recording-codec.ts`, drift-locked by the recording.v1 goldens. | — |
| `webhooks/` | **O-MTG-2** — outbound delivery behind `WebhookSink`: HMAC, SSRF guard, event-filter, redis retry (webhook.v1). A library brick (lazily exposed). | — |
| `scheduling/` | **O-MTG-3** — compile a `ScheduledBot{cron\|at}` into a `POST /bots` job, Clock-gated (schedule.v1). A library brick (lazily exposed). | — |

Each module is **port-driven** (a `build_router(...)` over injected ports + in-memory `fakes` +
production `adapters`), so the SAME app runs with real adapters in prod and in-process fakes in the
gateway conformance harness — the conformance assertions therefore drive THIS shipped app.

Never imports another domain's internals: the SQLAlchemy models are a self-contained mirror
(`sessions/models.py`), and contracts (`meetings/contracts/{lifecycle,webhook,invocation}.v1`,
`runtime/contracts/runtime.v1`, `gateway/contracts/api.v1`) are loaded **by path** (the seam). Deps
are pinned in `pyproject.toml`; `scheduling` (croniter) + `webhooks` (redis) are exposed lazily so a
consumer that only drives the REST surface needs neither.

## P3 seams (NOT built here)
continue_meeting, max-bots / join-retry (bot_spawn), the always-on segments consumer loop
(collector), the master byte-stream download (recordings), and the production composition root that
wires the real adapters. Behavioral diagnostics + fixtures are P3.
