# meeting-api — the cloud control-plane carve (Python)

The control-plane pieces of the cloud meeting-api that prove out **autonomously** — no docker,
no live meeting, no real bot. Three bricks, each behind a port, each with an eval that rides the
0.12 gate suite:

1. **Recording master codec** (recording.v1) — the Python twin of `@vexa/recording`'s
   `buildRecordingMaster`. Assembles already-ordered recording.v1 chunks into one master media
   file (`webm` = byte-concat in seq order; `wav` = RIFF header-merge), held byte-identical to the
   SHARED golden vectors (`meetings/modules/recording/src/contracts/golden/`) so cloud and desktop
   assemblers cannot drift.
2. **Lifecycle receiver + meeting-state machine** (O-MTG-1, lifecycle.v1) — ingests the bot's
   domain-status events, validates each at the seam, drives each meeting record's FSM
   (`joining → awaiting_admission → active → completed|failed`), rejects illegal transitions, and
   records terminal `failure_stage`/`completion_reason` server-side. See `src/meeting_api/lifecycle/`.
3. **Webhooks** (O-MTG-2, webhook.v1) — outbound delivery (system + per-client) behind a
   `WebhookSink` port: HMAC sign/verify over `ts.payload`, SSRF URL-guard, per-client event-filter,
   and a redis-backed exponential-backoff retry queue + worker sweep. See `src/meeting_api/webhooks/`.

> **The cloud meeting-api service is main's real one** (`services/meeting-api`, FastAPI + Postgres +
> the collector + MinIO), reused via the published `vexaai/meeting-api` image — see
> `docs/RELEASE-PLAN.md` (keep-vs-change) and Learning #20. This carve **derives from that parent's
> real behavior and reimplements clean** for the control-plane seams — the in-memory FSM store and
> the fakeredis retry queue keep the evals fast and dependency-free. The public surface is sealed in
> `gateway/contracts/{api.v1, ws.v1}`.

## Surface
Front door `meeting_api/__init__.py` → `build_recording_master`, `lifecycle`, `webhooks`.
```
src/meeting_api/recording_codec.py   the master codec (twin of recording-codec.ts, golden-locked)
src/meeting_api/lifecycle/           O-MTG-1: LifecycleSink + FSM + the FastAPI receiver (create_app)
src/meeting_api/webhooks/            O-MTG-2: WebhookSink + HMAC + SSRF guard + event-filter + retry
tests/                               the evals (L1 conformance · O-MTG-1 · O-MTG-2)
```

## Contracts (the seam, loaded by path)
- `meetings/contracts/lifecycle.v1` — SEALED. The receiver validates incoming events against it.
- `meetings/contracts/webhook.v1` — **UNSEALED** (in development). The delivery envelope + signed-
  header scheme. Sealing is the human `lane:contract` step (`pnpm seal:contracts`).

## Run the tests
```bash
uv run pytest -q        # autonomous; discovered by gate:python
```
`gate:health` (a meeting-api `/health`, exposed by the lifecycle receiver) and any named webhook gate
are the orchestrator's to wire; this carve owns the receivers + the evals.
