# meeting-api — the cloud control-plane (Python, modular monolith)

The unified cloud meeting-api: **ONE uvicorn-able app** (`meeting_api.create_app`) assembled from
front-doored modules, each behind a port so the whole app proves out **autonomously** — no docker, no
live meeting, no real bot. This is the v0.12 unification (P2) of the parent `services/meeting-api`'s
fragmented routers into one modular monolith, with the transcription-collector **folded in** as a
module (it is no longer a separate service).

## The unified app — `create_app(...)` composes:
1. **lifecycle** (O-MTG-1, lifecycle.v1) — ingests the bot's domain-status events, validates each at
   the seam, drives each meeting record's FSM (`joining → awaiting_admission → active →
   completed|failed`), rejects illegal transitions. `POST /bots/internal/callback/lifecycle`.
2. **bot_spawn** (invocation.v1 + runtime.v1) — `POST /bots`: build the invocation + mint the
   MeetingToken + spawn the meeting-bot over the runtime kernel, eager-creating the `MeetingSession`.
3. **collector** (api.v1) — the **folded-in** transcript backend (was the standalone
   transcription-collector): `GET /transcripts/…`, `GET /meetings`, `POST /ws/authorize-subscribe`,
   plus the `transcription_segments` → `tc:…:mutable` consumer.
4. **recordings** (recording.v1) — chunk upload + finalize → master in `meeting.data` JSONB. The
   master codec (`build_recording_master`) is the golden-locked Python twin of `@vexa/recording`'s
   `buildRecordingMaster`.
5. **sessions** — the `MeetingSession` model + the shared SQLAlchemy mirror every module binds.
6. **webhooks** (O-MTG-2, webhook.v1) + **scheduling** (O-MTG-3, schedule.v1) — library bricks driven
   by the flows above (lazily exposed; not on the unified app's HTTP path in the core carve).

> The cloud meeting-api derives from main's real behavior and **reimplements clean** for the
> control-plane seams — in-memory stores + fakeredis keep the evals fast and dependency-free; each
> module is port-driven (`build_router` over injected ports + in-memory `fakes` + production
> `adapters`), so the gateway conformance harness drives THIS shipped app. The public surface is
> sealed in `gateway/contracts/{api.v1, ws.v1}`.

## Surface
Front door `meeting_api/__init__.py` → `create_app`, `build_recording_master`, and the
`lifecycle` / `bot_spawn` / `collector` / `recordings` / `sessions` / `webhooks` / `scheduling`
modules. See `src/meeting_api/README.md` for the module table.
```
src/meeting_api/app.py        create_app(...) — the unified app (composes the modules + /health)
src/meeting_api/lifecycle/    O-MTG-1: LifecycleSink + FSM + the FastAPI receiver
src/meeting_api/bot_spawn/    POST /bots → invocation.v1 + runtime.v1 spawn + eager MeetingSession
src/meeting_api/collector/    the folded-in transcript backend (api.v1) + segments consumer
src/meeting_api/recordings/   chunk upload + finalize → meeting.data JSONB (recording.v1)
src/meeting_api/sessions/     the MeetingSession model + the shared SQLAlchemy mirror
src/meeting_api/recording_codec.py   the master codec (twin of recording-codec.ts, golden-locked)
src/meeting_api/webhooks/     O-MTG-2: WebhookSink + HMAC + SSRF guard + event-filter + retry
src/meeting_api/scheduling/   O-MTG-3: ScheduledBot{cron|at} → POST /bots job (Clock-gated)
tests/                        the evals (L1 codec · lifecycle · webhooks · scheduling · bot_spawn ·
                              recordings · collector · the unified app)
```

## Contracts (the seam, loaded by path)
- `meetings/contracts/{lifecycle,invocation,webhook}.v1`, `runtime/contracts/runtime.v1`,
  `gateway/contracts/api.v1` — validated / conformed-to by the modules; never edited here.

## P3 seams (NOT built in this carve)
Lifecycle diagnostics + fixtures, `continue_meeting`, join-retry, max-bots, the always-on segments
consumer loop, the recording master byte-stream download, and the production composition root that
wires the real adapters.

## Run the tests
```bash
uv run pytest -q        # autonomous; discovered by gate:python
```
`gate:health` points at the unified app's `/health` (the collector is no longer a separate HTTP
service — `gate:health`'s service count is one lower than before the fold, as expected).
