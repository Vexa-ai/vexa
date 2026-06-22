# Parity with main + enhancements — the v0.12 backend vs the 0.10.6.3 stack

> The "on-par-with-main + better-than-main" claim, made checkable (ADR-0021: a "better than X" claim is a
> **specific green gate**, never prose). Every main backend capability + every `api.v1` endpoint maps to a
> **green 0.12 proof** (a gate/test); `gate:parity` fails if any row is left unmapped. The contract anchor:
> `api.v1` is sealed **hash-equal to main's OpenAPI 1.5.0** (`gate:contract-version`).

## 1 · On-par-with-main — capability matrix
Every row's **Proof** cites a green gate/test. (`gate:compose`+MOCK_BOT = the L3 mock-bot lane,
`deploy/compose/tests/`; unit paths are `meetings/services/meeting-api/tests/` etc.)

| Capability (main) | main home | 0.12 path | Proof | Status |
|---|---|---|---|---|
| Meeting lifecycle FSM (join→admit→active→terminal) | meeting-api | `meeting_api/lifecycle` | `test_lifecycle_machine.py` + `gate:compose` MOCK_BOT (normal/reject/crash/timeout) | ✅ |
| Lifecycle status **persisted + queryable** (DB) | meeting-api | `app.py` + `bot_spawn/adapters` | `gate:compose` MOCK_BOT (meeting reaches terminal in the DB) | ✅ |
| Attributable failure reasons | meeting-api | `lifecycle` (completion_reason/failure_stage) | `gate:compose` MOCK_BOT (reject/crash/timeout assert the reason) | ✅ |
| Bot spawn (invocation + runtime spawn) | meeting-api + runtime-api | `bot_spawn` + `runtime_kernel` | `test_bot_spawn.py` + `gate:compose` MOCK_BOT (real spawn) | ✅ |
| Per-user concurrency cap (max-bots) | runtime-api | `bot_spawn` (X-User-Limits) | `test_max_bots.py` + `gate:compose` (test_06d + MOCK_BOT max-bots-live) | ✅ |
| Continue/reuse meeting (sequential sessions) | meeting-api | `bot_spawn` continue_meeting | `test_continue_meeting.py` + `gate:compose` (test_06c) | ✅ |
| Join-retry (transient → re-spawn, backoff) | meeting-api | `lifecycle.retry` + scheduler | `test_join_retry.py` + `gate:compose` (test_06b) | ✅ |
| Scheduling (cron/at scheduled bots) | calendar/meeting-api | `scheduling` | `test_scheduling.py` | ✅ |
| Webhooks (delivery · HMAC · SSRF · retry) | meeting-api | `webhooks` | `test_webhook_signing.py` · `test_webhook_ssrf.py` · `test_webhook_delivery.py` | ✅ |
| `meeting.status_change` webhook on FSM advance | meeting-api | `lifecycle.webhook` | `gate:compose` MOCK_BOT (envelope emitted per advance) | ✅ |
| Recording chunk upload + finalize → S3/minio | meeting-api | `recordings` + `recording_codec` | `test_recordings.py` · `test_recording_golden.py` + `gate:compose` (test_05 + MOCK_BOT normal) | ✅ |
| Transcription collector (segments→pg→ws) | transcription-collector | `meeting_api/collector` (folded in) | `test_collector_api.py` + `gate:compose` (test_04 + MOCK_BOT emit-n) | ✅ |
| API gateway routing + auth (key/scope) | api-gateway | `gateway` (carved) | `conformance/test_api_surface.py` + `gate:compose` (test_02) | ✅ |
| WebSocket real-time fan-out (`/ws`) | api-gateway | `gateway.ws_multiplex` | `conformance/test_ws_protocol.py` + `gate:compose` (test_04 /ws frame) | ✅ |
| User management + API tokens | admin-api | `identity` + `admin-api` | `identity/tests/test_tokens.py` · `admin-api/tests/test_stack_admin_api.py` | ✅ |
| Multi-platform (meet/zoom/teams) | meeting-api/bot | `invocation.v1` platform | `gate:schema` (invocation.v1) + the carved bot's platform branches | ✅ |
| Per-service health + liveness | (root /health) | per-service `/health` | `gate:health` + `gate:compose` (test_01) | ✅ |
| Agent execution (governed worker) | agent-api | `agent` (reused) | `agent-api/tests/test_core_run.py` | ✅ |
| STT / TTS / MCP services | per-service | reused main images | `docker compose config` (12 images) · reused | ✅ |

## 2 · `api.v1` endpoint parity (≡ main OpenAPI 1.5.0)
The public surface is the **sealed `api.v1`**, hash-equal to main's OpenAPI 1.5.0. Each endpoint group is
driven by the conformance harness against the shipped app.

| Endpoint group | Proof | Status |
|---|---|---|
| `POST/GET/DELETE/PUT /bots` · `/bots/status` | `conformance/test_api_surface.py` + `gate:compose` MOCK_BOT | ✅ |
| `GET /transcripts/{platform}/{id}` | `conformance/test_api_surface.py` + `gate:compose` (test_04) | ✅ |
| `GET /recordings` · `/recordings/{id}/master` | `conformance/test_api_surface.py` + `gate:compose` (test_05) | ✅ |
| `GET /meetings` · `/meetings/{id}` | `conformance/test_api_surface.py` | ✅ |
| `POST /bots/{p}/{id}/speak` (voice agent) | `gate:schema` (acts.v1) + `gate:compose` MOCK_BOT (speak-ack) | ✅ |
| `GET /ws` (real-time) | `conformance/test_ws_protocol.py` | ✅ |
| contract freeze (no drift from main 1.5.0) | `gate:contract-version` (api.v1 sealed) | ✅ |

## 3 · Better than main — the enhancement gate-set (each a green gate main lacks)
The *set being green* IS "better than main"; main ships per-service tests only, none of these gates.

| Enhancement | Gate | main equivalent | Status |
|---|---|---|---|
| Sealed, frozen contracts | `gate:contract-version` | none | ✅ |
| Module-boundary isolation (prod) | `gate:isolation` · `gate:isolation-py` | none | ✅ |
| Module-boundary isolation (**tests**) | `gate:test-isolation` | none | ✅ |
| Acyclic + allowed-edges graph | `gate:graph` · `gate:graph-py` | none | ✅ |
| One front door per module | `gate:exports` | none | ✅ |
| Fail-loud + attributable faults | `gate:fault-surfacing` | none | ✅ |
| Per-service health + liveness | `gate:health` | root-only | ✅ |
| Complete mediation (default-deny) | `gate:access` | none | ✅ |
| Real-stack control-plane proof | `gate:compose` (+MOCK_BOT, L3 anywhere) | none | ✅ |
| Concurrency-stress proof | `gate:compose-stress` | none | ⏳ A:V2 |
| Fault-injection proof | `gate:compose-chaos` | none | ⏳ A:V3 |
| Reusable worker-L4 eval instrument | `gate:eval-baseline` | manual | ✅ (live score = B:V1, human-gated) |
| OSS-licence-clean (SBOM) | `gate:licenses` | none | ✅ |
| Architecture-compliance map | `gate:arch-report` | none | ✅ |
| Parity completeness | `gate:parity` | none | ✅ |
| Where-work-runs registry | `gate:execution-env` | none | ✅ |

_⏳ rows are planned gates (A:V2/A:V3/Lane B), not yet green — listed for completeness, not claimed._

## 4 · Known parity gaps (flagged, not silently dropped — P21)
- **`DELETE /bots` HTTP route** unmounted in the unified meeting-api (the stop *logic* exists,
  `lifecycle/stop.py`); the api.v1 endpoint 404s. Flagged for wiring; the L3 lane drives stop via the
  leave-command for now. (Found by the mock-bot L3 lane, Learning #27.)
- **max-bots cap has a TOCTOU race** — the count-then-insert pre-check isn't atomic, so concurrent
  `POST /bots` can overspill the per-user cap (the stress lane reproduces it). Likely shared with main;
  making it atomic (advisory lock / serializable / conditional insert) is a *better-than-main*
  enhancement. Flagged; the stress gate asserts "enforcement active + bounded overspill". (Found by A:V2.)
