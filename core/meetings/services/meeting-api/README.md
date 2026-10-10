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
| verifies | api-gateway | `X-Vexa-Identity` ([`gateway-identity.v1`](../../../gateway/contracts/gateway-identity.v1), `src/meeting_api/identity_token.py`) | every `x-user-*` header (owner, bot limit, memberships, webhook) comes from the gateway's signature, or from the internal tier (`X-Internal-Secret`, agent-api acting for a person); anything else naming a person is a `401`. A worker's identity carries its dispatch's regime: `POST /bots`, the transcript-share mints, the workspace bind and the meeting/recording deletes refuse (`403 human_session_required`) a delegated identity whose regime is not `human` (`src/meeting_api/regime.py`) |
| verifies | api-gateway | the signed key scopes (`x-user-scopes`) against [`core/meetings/routes.v1.json`](../../routes.v1.json) (`src/meeting_api/route_scopes.py`) | a gateway-signed request is refused (`403 Insufficient scope for this endpoint`) when its key holds none of the scopes of the edge rows that reach the route it matched (a row reaches its own path or its `upstream`), and on any route no row reaches except the edge's own `POST /ws/authorize-subscribe`. The internal tier and the self-authenticating callbacks are not keys and are not checked here |
| calls | api-gateway / agent-api | `POST /bots` | request a bot (platform + native id + per-user webhook cfg) → eager `MeetingSession` |
| calls | api-gateway / agent-api | `DELETE /bots/{platform}/{native}` | user-stop → leave command + workload teardown |
| calls | dashboard / agent-api | `GET /meetings` | the user's meetings (live + past), api.v1 `MeetingListResponse` |
| calls | dashboard / agent-api | `GET /transcripts/{platform}/{native}` | the meeting transcript, api.v1 `TranscriptionResponse` |
| calls | api-gateway `/ws` | `POST /ws/authorize-subscribe` | identity-scoped subscribe authorization |
| spawns-over | runtime kernel | `runtime.v1` (`RuntimeClient.create_workload`) | the meeting-bot workload (carries the `invocation.v1` BOT_CONFIG + MeetingToken); every call presents `RUNTIME_API_TOKEN` |
| consumes | meeting-bot | `POST /bots/internal/callback/lifecycle` | `lifecycle.v1` `LifecycleEvent` → FSM advance + DB persist. Admitted by the event's own session's MeetingToken (see below) or the internal tier (`X-Internal-Secret`); anything else is a `401` |
| consumes | meeting-bot | `POST /internal/recordings/upload` | recording chunks and signal tapes for the session named by `session_uid`. Admitted by that session's MeetingToken or the internal tier (`Bearer <INTERNAL_API_SECRET>`) |
| consumes | meeting-bot | `PUT /internal/browser-session/{session_uid}` | an authenticated bot's rotated browser session, a [`session-profile.v1`](../../contracts/session-profile.v1) `WritebackBody` (`src/meeting_api/session_profile`), at the URL the spawn names in the invocation (`sessionWritebackUrl`, authenticated spawns only). Admitted only by that session's MeetingToken, only while it is the live authenticated bot (the newest session spawned on `BOT_USERDATA_S3_PATH`, its meeting live or ended < 10 min), only SESSION_PROFILE files; stored with meeting-api's own S3 credentials. The bots' `BOT_S3_*` pair is read-only |
| consumes | runtime kernel | `POST /runtime/callback` | workload state/terminal ACK (CC5 synthetic `failed`). Admitted only with the runtime's `X-Runtime-Signature`, an HMAC over the event keyed from `RUNTIME_API_TOKEN` (`src/meeting_api/runtime_signature.py`) |
| calls (optional) | operator service authority | `service-authority.v1` over signed HTTP | allow/deny before spawn and at each one-minute active-service boundary; no hosted billing data |
| consumes | transcription worker | redis stream `transcription_segments` | raw `transcript.v1` segments → DB |
| publishes | api-gateway `/ws` | redis channel `tc:meeting:{id}:mutable` | the live mutable transcript bundle |
| publishes | api-gateway `/ws` | redis channel `bm:meeting:{id}:status` | ws.v1 `meeting.status` (BotStatus) on each FSM advance |
| produces | user webhook endpoint | `webhook.v1` envelope | `meeting.status_change` (signed, best-effort delivery) |

### The session token (MeetingToken)

The one credential a meeting bot holds (`src/meeting_api/meeting_token.py`). An HS256 JWT signed
with the MeetingToken key, which is derived from `ADMIN_TOKEN` (HMAC-SHA256 of it under the label
`vexa/meeting-token/v1`, fact `meeting-token-key-label`) and is never the admin secret itself. It is
minted by `POST /bots` for ONE bot session and bound to it: its `session_uid` claim is the spawn's
`connection_id`, the id the eager `MeetingSession` is keyed by, and it carries `exp`, the
MeetingToken's own `aud` and `scope`. It rides the bot's `invocation.v1` as `token`, so the bot
workload is its only holder; it expires after `MEETING_TOKEN_TTL_SECONDS` (default 5 h). The bot
presents it as `Authorization: Bearer <token>` on the three doors above: the lifecycle callback
(session = the event's `connection_id`), the recording/tape upload (session = the request's
`session_uid`) and, in authenticated mode, the browser-session write-back (session = the path's
`session_uid`). All three apply one rule, `admit_session`: a valid signature, an `exp` not passed, the
MeetingToken's `aud` and `scope` (each required), and bound to exactly that session. A token for
another session, or bound to none, is refused, so a bot can move and write only its own session. The
token's fourth use is the transcript: each `transcription_segments` entry carries `auth` (the token's
header.payload) and `sig` (HMAC of the payload keyed with the token), and the collector admits only
entries whose token names the meeting they write for (transcript.v1 `StreamEntry`). The bot's other
credential is the Redis user in its `redisUrl`, defined for its session alone; with
`REDIS_WORKLOAD_ACL=shared` the invocation carries meeting-api's own Redis connection instead, the one
case where a service credential is placed in an invocation. With no `ADMIN_TOKEN` (or no
`RUNTIME_API_TOKEN` for the runtime callback) a door refuses every caller; only the in-process test
harness opens them, with `create_app(open_callbacks=True)`.

## Contracts

**Owns:** `core/meetings/contracts/lifecycle.v1` · `core/meetings/contracts/transcript.v1` ·
`core/meetings/contracts/webhook.v1` · `core/meetings/contracts/invocation.v1` ·
`core/meetings/contracts/acts.v1` · `core/meetings/contracts/service-authority.v1`.
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
- ✅ delivered — opt-in `service-authority.v1` admission and active-service boundary; unset is
  explicit self-hosted allow-all, configured failure is closed
- 🟡 partial — production composition root wiring real adapters (DB/redis/MinIO) is P3; ports are in place
- ⬜ planned — `GET /meetings` is the source the terminal meetings list (live+past) will read via agent-api
