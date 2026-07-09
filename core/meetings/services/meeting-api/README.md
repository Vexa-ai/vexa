# meeting-api — the meetings domain service (Python)

## Purpose

The ONE uvicorn-able meetings service (modular monolith, P2): it owns **bot lifecycle**
(spawn a meeting-bot over `runtime.v1`, drive its FSM from `lifecycle.v1` callbacks, stop it),
the **transcription collector** (drain the `transcription_segments` redis stream → DB, publish the
live transcript), and the **read surface** the dashboard/agent query (`GET /meetings`, `GET /transcripts`).
Python because the meetings domain carves the deployed `bot-manager` + `transcription-collector`
and stays in their ecosystem (FastAPI + redis + DB).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| calls | api-gateway / agent-api | `POST /bots` | request a bot (platform + native id + per-user webhook cfg) → eager `MeetingSession` |
| calls | api-gateway / agent-api | `DELETE /bots/{platform}/{native}` | user-stop → leave command + workload teardown |
| calls | dashboard / agent-api | `GET /meetings` | the user's meetings (live + past), api.v1 `MeetingListResponse` |
| calls | dashboard / agent-api | `GET /transcripts/{platform}/{native}` | the meeting transcript, api.v1 `TranscriptionResponse` |
| calls | api-gateway `/ws` | `POST /ws/authorize-subscribe` | identity-scoped subscribe authorization |
| spawns-over | runtime kernel | `runtime.v1` (`RuntimeClient.create_workload`) | the meeting-bot workload (carries the `invocation.v1` BOT_CONFIG + MeetingToken) |
| consumes | meeting-bot | `POST /bots/internal/callback/lifecycle` | `lifecycle.v1` `LifecycleEvent` → FSM advance + DB persist |
| consumes | runtime kernel | `POST /runtime/callback` | workload state/terminal ACK (CC5 synthetic `failed`) |
| consumes | transcription worker | redis stream `transcription_segments` | raw `transcript.v1` segments → DB |
| publishes | api-gateway `/ws` | redis channel `tc:meeting:{id}:mutable` | the live mutable transcript bundle |
| publishes | api-gateway `/ws` | redis channel `bm:meeting:{id}:status` | ws.v1 `meeting.status` (BotStatus) on each FSM advance |
| produces | user webhook endpoint | `webhook.v1` envelope | `meeting.status_change` (signed, best-effort delivery) |

## Contracts

**Owns:** `core/meetings/contracts/lifecycle.v1` · `core/meetings/contracts/transcript.v1` ·
`core/meetings/contracts/webhook.v1` · `core/meetings/contracts/invocation.v1` ·
`core/meetings/contracts/acts.v1`.
**Consumes:** `core/runtime/contracts/runtime.v1` (spawn the bot workload) and api.v1
(`MeetingListResponse` / `TranscriptionResponse` response shapes). All sealed in `contracts.seal.json`.

## Isolated evaluation

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

`tests/` runs in-process against `create_app(...)` with every port falling back to an in-memory fake
(no DB, no redis, no MinIO, no runtime kernel) — so the conformance harness drives the shipped app.
Levels: **L1** contract conformance (`test_contract_conformance`, `collector_contracts`) ·
**L2** unit (`test_lifecycle_machine`, `test_collector_api`, `test_ingest`) ·
**L3** integration (`test_lifecycle_seam`, `test_webhook_delivery`, `test_stress_seam`).

## Status

- ✅ delivered — `POST /bots` (invocation.v1 + runtime.v1 spawn) · `DELETE /bots/{platform}/{native}` user-stop
- ✅ delivered — `lifecycle.v1` callback receiver + FSM, durable DB persist + restart rehydration
- ✅ delivered — collector: `transcription_segments` → DB, publish `tc:meeting:{id}:mutable` + `bm:meeting:{id}:status`
- ✅ delivered — `GET /meetings` (live + past per user) · `GET /transcripts` · `POST /ws/authorize-subscribe`
- ✅ delivered — `webhook.v1` `meeting.status_change` signed per-user delivery
- 🟡 partial — production composition root wiring real adapters (DB/redis/MinIO) is P3; ports are in place
- ⬜ planned — `GET /meetings` is the source the terminal meetings list (live+past) will read via agent-api
