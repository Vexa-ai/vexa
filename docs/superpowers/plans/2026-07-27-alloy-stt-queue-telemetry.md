# ALLOY STT Queue Telemetry Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` when continuing this plan.
> The original no-worktree instruction applied only while preserving the inherited dirty `main`
> checkout. That work was captured in a local checkpoint; all current and future edits use a
> session-owned worktree based on the selected checkpoint ref.

**Status at integration ref:** source and sealed-contract implementation is present; focused
offline evidence and standard-runner wiring are complete. Two explicit disposable-Redis lanes,
independent review, a clean-image provenance check, and real Google Meet acceptance remain open.

**Goal:** Provide an opt-in, owner-scoped STT queue monitor that reports real Whisper execution,
backlog, lag, RTF, and failures on every Vexa Terminal screen without changing upstream behavior
when disabled.

**Architecture:** The per-channel scheduler owns pending and supersede transitions.
`TranscriptionClient` reports the real limiter slot lifecycle after semaphore acquisition, and one
tracker consolidates those events into a per-meeting snapshot. The bot publishes the snapshot to a
versioned Redis key. Meeting API reads exact keys for owner-owned active meetings, validates them
against the sealed contract, and computes the aggregate and health. Gateway forwards the
authenticated route. One Terminal poller reads the server aggregate once per second only while the
page is visible.

**Approved specification:** `docs/ALLOY-STT-TELEMETRY-DESIGN.md`

## Fixed delivered contract

- All behavior is enabled only by exact opt-in flags. The upstream-compatible defaults are
  `ALLOY_STT_MAX_CONCURRENCY=0`, `ALLOY_STT_CHANNEL_BACKPRESSURE=0`,
  `ALLOY_STT_LANGUAGE_MODE=configured`, `ALLOY_STT_TELEMETRY=0`,
  `NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT=0`, and `ALLOY_SKIP_HF_CACHE_WARM=0`.
- The approved local pilot overrides those values explicitly as `1/1/auto/1/1/1`.
- `ALLOY_STT_MAX_CONCURRENCY` limits one bot process. It is not a cross-process, cross-meeting, or
  Whisper-service-wide semaphore.
- The Redis key is `alloy:stt:telemetry:v1:{meeting_id}` with a 15-second TTL.
- The backend route is `GET /alloy/stt/status` in Meeting API and Gateway. The Terminal proxy is
  `GET /api/alloy/stt/status`.
- With telemetry disabled, Meeting API and Gateway do not register the route. Terminal mounts no
  telemetry hook, subscription, timer, or fetch and renders the upstream reset footer.
- Meeting API reads exact keys for owner-owned active meetings only. Transcript or workspace
  sharing does not grant telemetry access.
- Sealed contract `alloy-stt-telemetry.v1` owns `Snapshot`, `Aggregate`, and `StatusResponse`.
  The response fields are `version`, `enabled`, `available`, `updated_at_ms`, `aggregate`,
  `meetings`, and `error`.
- Meeting API computes aggregate values and health. Terminal renders that aggregate and does not
  rederive it.
- Invalid, incompatible, version-mismatched, non-finite, or key/payload-mismatched snapshots are
  silently omitted. Redis transport failure returns the sealed unavailable response and never
  changes transcription.
- Health is red for `last_error`, age `>5s`, or lag `>15s`; amber otherwise for age `>3s`, lag
  `>=5s`, RTF `>1`, or a first request with `active_requests > 0` and `processed_windows == 0`;
  green otherwise; muted when there are no valid snapshots. Aggregate health is the worst meeting
  health.
- The Terminal polls once per second only while visible. Timer/visibility triggers reuse one
  request promise per active polling generation. Stop/restart may start a fresh request while the
  invalidated generation's network call is still pending; generation fencing ignores the old
  result. The hidden and disabled paths make no request, and transport failure retains the last
  valid response.
- Auto-language removes the pinned language parameter and delegates detection to the configured
  backend/model. Bundled local STT needs the separate non-ALLOY make override
  `WHISPER_MODEL=Systran/faster-whisper-small`; the default and example `.en` models are
  English-only. Real Russian, English, and code-switch acceptance remains open.
- Alloy-owned blocks/comments use `ALLOY:` and runtime diagnostics use `[ALLOY]`.
- DRY and SOLID are applied proportionately: the sealed contract owns wire semantics, the scheduler
  and STT client own their real transitions, one tracker owns metrics, Meeting API owns aggregate
  semantics, and one Terminal store owns polling. No speculative abstraction or unrelated
  refactor is authorized.

## Evidence interpretation

Checked rows below mean that the integrated source and its named focused evidence exist on the
selected integration ref. They do not prove that a newly built image is running, that a real
Redis lane passed, or that a real multilingual meeting completed. Historical RED instructions
have been reconciled into observed production-boundary checks rather than being presented as
commands that still need to fail.

The TypeScript compile portion of the focused package evidence is complete. Some package scripts
also contain POSIX `rm`/`cp` post-steps; invoking those scripts directly in Windows PowerShell
still requires a compatible POSIX command environment. That environment caveat does not downgrade
the already completed TypeScript compile evidence.

## File Map

| File | Responsibility |
| --- | --- |
| `core/meetings/contracts/alloy-stt-telemetry.v1/alloy-stt-telemetry.schema.json` | Sealed `Snapshot`, `Aggregate`, and `StatusResponse` wire contract |
| `core/meetings/contracts/alloy-stt-telemetry.v1/golden/*.json` | Available, unavailable, disabled, active, waiting, and aggregate goldens |
| `core/meetings/contracts/alloy-stt-telemetry.v1/validate.mjs` | Contract and golden validator |
| `core/meetings/modules/gmeet-pipeline/src/alloy-stt-telemetry.ts` | Single in-memory per-meeting queue-metrics owner |
| `core/meetings/modules/gmeet-pipeline/src/alloy-stt-telemetry.test.ts` | Tracker transition and contract tests |
| `core/meetings/modules/gmeet-pipeline/src/gmeet-pipeline.ts` | Per-channel pending/supersede instrumentation and observer composition |
| `core/meetings/modules/gmeet-pipeline/src/alloy-channel-backpressure.test.ts` | Active-turn preservation and real scheduler/backpressure evidence |
| `core/meetings/modules/whisper/src/transcription-client.ts` | Per-bot limiter and real slot lifecycle observer |
| `core/meetings/modules/whisper/src/concurrency-observer.test.ts` | Waiting/start/finish ordering and observer fault-isolation evidence |
| `core/meetings/services/bot/src/adapters/alloy-stt-telemetry-redis.ts` | Versioned, rate-limited, bounded Redis snapshot publisher |
| `core/meetings/services/bot/src/adapters/alloy-stt-telemetry-redis.test.ts` | Offline lifecycle checks and explicit `--real-redis` integration lane |
| `core/meetings/services/bot/src/index.ts` | Exact-flag tracker and publisher composition |
| `core/meetings/services/bot/src/pipeline.ts` | Optional tracker injection into the real gmeet pipeline |
| `core/meetings/services/bot/package.json` | Standard offline runner plus explicit `test:redis` lane |
| `core/meetings/services/meeting-api/src/meeting_api/collector/alloy_stt_telemetry.py` | Exact-key Redis reader and sealed snapshot validation |
| `core/meetings/services/meeting-api/src/meeting_api/collector/alloy_stt_status.py` | Server-owned aggregate and health |
| `core/meetings/services/meeting-api/src/meeting_api/collector/app.py` | Strict owner-only active-meeting route |
| `core/meetings/services/meeting-api/tests/test_alloy_stt_status.py` | Owner, aggregate, health, omission, and unavailable tests |
| `core/meetings/services/meeting-api/tests/test_alloy_stt_telemetry.py` | Explicit real-Redis route integration lane |
| `core/meetings/services/meeting-api/pyproject.toml` | Standard pytest registration and explicit Redis marker |
| `core/gateway/services/gateway/src/gateway/app.py` | Authenticated `GET /alloy/stt/status` forwarding |
| `core/gateway/services/gateway/src/gateway/adapters.py` | Exact opt-in Gateway route composition |
| `core/gateway/services/gateway/src/gateway/config.v1.json` | Gateway deployment-config declaration |
| `clients/terminal/src/app/api/alloyTelemetryMode.ts` | Exact server-runtime opt-in predicate |
| `clients/terminal/src/app/api/[...path]/route.ts` | Conditional Terminal-to-Gateway proxy routing |
| `clients/terminal/src/workbench/alloySttTelemetry.ts` | Sealed response parsing and one visibility-aware polling owner |
| `clients/terminal/src/workbench/AlloySttTelemetryMonitor.tsx` | Server aggregate footer and per-meeting details |
| `clients/terminal/src/workbench/Workbench.tsx` | Global monitor mount and upstream reset-layout fallback |
| `clients/terminal/src/workbench/__tests__/alloySttTelemetry.test.ts` | Poll cadence, visibility, parsing, and retention tests |
| `clients/terminal/src/workbench/__tests__/alloySttTelemetry.resilience.test.ts` | Restart overlap and stale-generation result fencing tests |
| `clients/terminal/src/workbench/__tests__/AlloySttTelemetryMonitor.test.tsx` | Footer, aggregate-health, details, and disabled fallback tests |
| `clients/terminal/src/app/__tests__/alloyTelemetryRuntime.test.tsx` | Runtime opt-in and hook-free disabled composition tests |
| `deploy/lite/entrypoint.sh` | Lite runtime default-zero telemetry export |
| `deploy/lite/bin/vexa-bot-launch` | Default-zero per-bot flags and explicit propagation |
| `deploy/lite/Makefile` | Runtime and build flag defaults |
| `deploy/lite/tests/test_alloy_opt_in.py` | Six-flag default-zero and explicit opt-in composition tests |
| `core/meetings/services/meeting-api/src/meeting_api/config.v1.json` | Meeting API telemetry deployment contract |
| `architecture.calm.json` | Redis carrier, ownership, API, and Terminal dataflow |
| Package runner files | Standard focused tests for tracker, slot observer, publisher, API, and Terminal |
| `docs/ALLOY-CUSTOMIZATIONS.md` | Switch defaults, explicit pilot profile, and rollback |
| `docs/ALLOY-STT-TELEMETRY-DESIGN.md` | Delivered behavioral design and evidence boundary |
| `deploy/lite/README.md` | Operator activation, endpoints, and interpretation |

---

### Task 1: Sealed Contract and Queue Telemetry Tracker

**Expected:** one versioned contract and one tracker own the cross-language wire fields and the
in-memory queue state; no audio payload is retained.

- [x] **Step 1: Add sealed `alloy-stt-telemetry.v1` schema and representative goldens.**

  The contract covers `Snapshot`, `Aggregate`, and `StatusResponse`, including disabled and
  dependency-unavailable responses.

- [x] **Step 2: Add exact tracker transition tests.**

  The focused nodes cover queued-to-active movement, same-channel replacement, lag from audio
  timeline positions, EMA RTF, balanced failure, and recovery.

- [x] **Step 3: Implement the minimal per-meeting tracker.**

  Pending and active state are keyed by request/channel identity; only durations, timestamps,
  counters, and bounded errors are retained.

- [x] **Step 4: Validate tracker invariants against the sealed snapshot schema.**

  Current counters remain non-negative and cumulative counters are limited to processed and
  superseded windows.

- [x] **Step 5: Export only the public tracker types and factory.**

  Internal mutable maps are not exported.

- [x] **Step 6: Run the focused tracker contract and TypeScript compile evidence.**

  Standard package runners include the tracker node.

---

### Task 2: Real Scheduler and Whisper Slot Instrumentation

**Expected:** waiting state comes from the scheduler, while active state and RTF begin only after
the real per-bot limiter grants a `TranscriptionClient` slot.

- [x] **Step 1: Add the one-channel rotation regression through the production pipeline.**

  The controlled external STT promise proves that a rotated active turn is finalized once, the
  successor turn is finalized once, and no dangling draft remains.

- [x] **Step 2: Add the optional Whisper execution observer.**

  `waiting`, `started`, and `finished` reflect FIFO slot lifecycle; observer callback failure
  cannot alter STT output or prevent slot release.

- [x] **Step 3: Connect scheduler pending/supersede events and the real slot observer to one tracker.**

  Queue waiting is excluded from processing duration and RTF.

- [x] **Step 4: Run focused success, failure, cancellation, and supersede boundaries.**

  The tracker returns to `active_requests=0` and `waiting_channels=0` after completion.

- [x] **Step 5: Record focused package compile evidence and the Windows POSIX post-step caveat.**

  TypeScript compilation is complete; POSIX `rm`/`cp` package-script post-steps require a
  compatible shell when invoked from Windows.

---

### Task 3: Fault-Isolated Redis Publisher and Bot Composition

**Expected:** an enabled bot publishes one current snapshot to
`alloy:stt:telemetry:v1:{meeting_id}` with TTL 15 seconds; telemetry never controls bot teardown
or transcription.

- [x] **Step 1: Add offline publisher lifecycle and bounded teardown tests.**

- [x] **Step 2: Validate versioned key construction, one in-flight write, refresh, and `DEL`.**

- [x] **Step 3: Implement `alloy-stt-telemetry-redis.ts` with immediate start publish and bounded periodic refresh.**

- [ ] **Step 4: Run the explicit publisher lane against a disposable Redis database.**

  Run only with a disposable target:

  ```powershell
  $env:ALLOY_TEST_REDIS_URL='redis://127.0.0.1:6379/<disposable-db>'
  pnpm --filter @vexa/bot run test:redis
  ```

  Expected: sealed snapshot exists, TTL is positive and refreshes, overwrite is visible, and
  `stop()` deletes the key. Stop at 45 seconds or the first schema/TTL/cleanup mismatch; retain
  output and do not repeat unchanged.

- [x] **Step 5: Compose tracker and publisher only when `ALLOY_STT_TELEMETRY` is exactly `1`.**

- [x] **Step 6: Propagate default-zero runtime flags to newly spawned Lite bots.**

- [x] **Step 7: Wire offline publisher checks into the standard bot test runner.**

  The real-Redis branch remains explicit behind `--real-redis` and is never silently skipped as
  evidence of an integration PASS.

---

### Task 4: Strict Owner-Only Meeting API Aggregate

**Expected:** the route exists only when enabled, reads exact versioned keys for owner-owned active
meetings, silently omits invalid neighbors, and returns the server-computed sealed aggregate.

- [x] **Step 1: Add sealed parsing, aggregation, health, and unavailable-response tests.**

- [x] **Step 2: Add strict owner-only tests that exclude transcript/workspace shares and other tenants.**

- [x] **Step 3: Implement strict snapshot validation and silent incompatible-record omission.**

  Invalid, version-mismatched, non-finite, or key/payload-mismatched records are not returned and
  do not invalidate valid neighbors.

- [x] **Step 4: Register `GET /alloy/stt/status` only with the enabled reader dependency.**

  Owner IDs come from the trusted server identity boundary; browser-supplied meeting ownership
  claims are not accepted.

- [x] **Step 5: Implement one bounded `MGET` over exact owner-owned active meeting keys.**

  Redis is never scanned.

- [x] **Step 6: Compute aggregate counts, maxima, and health in Meeting API.**

  Terminal does not maintain a second aggregate implementation.

- [ ] **Step 7: Run the owner route through a disposable real Redis database.**

  ```powershell
  $env:ALLOY_TEST_REDIS_URL='redis://127.0.0.1:6379/<disposable-db>'
  Push-Location core/meetings/services/meeting-api
  uv run pytest -m alloy_real_redis tests/test_alloy_stt_telemetry.py -q
  Pop-Location
  ```

  Expected: only the owner's active valid snapshot is returned; other-owner, ended, and malformed
  rows are absent. Stop at 45 seconds or the first ownership/schema mismatch.

- [x] **Step 8: Wire focused API and contract nodes into the standard pytest runner.**

  The explicit `alloy_real_redis` marker remains opt-in and is not counted as PASS unless selected
  with a configured disposable Redis URL.

---

### Task 5: Authenticated Gateway and Configuration Governance

**Expected:** exact telemetry opt-in registers a thin authenticated Gateway route and default-zero
composition leaves it absent.

- [x] **Step 1: Add forwarding tests for authentication, stripped client identity, and injected owner identity.**

- [x] **Step 2: Add the thin Gateway `GET /alloy/stt/status` forwarder through the existing security funnel.**

- [x] **Step 3: Declare Meeting API and Gateway opt-in seams in `config.v1`.**

- [x] **Step 4: Seal the Redis carrier and owner/API/Terminal flow in `architecture.calm.json`.**

---

### Task 6: One Visibility-Aware Terminal Poller and Proxy

**Expected:** the enabled Terminal uses one generation-fenced poller for
`/api/alloy/stt/status`. Triggers within the active generation reuse its promise; restart may issue
a fresh request before the invalidated generation's network call settles. The disabled Terminal
creates no telemetry lifecycle.

- [x] **Step 1: Add parser, cadence, visibility, stale-retention, generation reuse, and restart-fencing tests.**

- [x] **Step 2: Implement one module-level polling owner using the sealed response.**

  It polls once per second only while visible and retains the last valid response after transport
  failure.

- [x] **Step 3: Add exact runtime opt-in routing through the Terminal catch-all proxy.**

  Enabled requests reach Gateway `/alloy/stt/status`; disabled composition leaves the meetings
  proxy branch unavailable.

- [x] **Step 4: Wire focused Terminal telemetry nodes into the standard Vitest runner.**

---

### Task 7: Global Footer Monitor and Upstream Fallback

**Expected:** enabled composition renders the server aggregate and per-meeting details; disabled
composition renders the original `reset layout` control without mounting a telemetry hook.

- [x] **Step 1: Add monitor tests for connecting, idle, unavailable, aggregate health, details, and fallback.**

- [x] **Step 2: Implement `AlloySttTelemetryMonitor.tsx` with the actual compact label.**

  Example:

  ```text
  STT 2 · 1 active · 2 waiting · 18.4s queued · lag 26.0s · RTF 1.42 · health red
  ```

- [x] **Step 3: Mount the monitor once in `Workbench.tsx`.**

- [x] **Step 4: Render server-owned aggregate health and keep per-meeting classification presentation-only.**

- [x] **Step 5: Verify exact-flag disabled composition has no hook, subscription, timer, or request.**

---

### Task 8: Deployment and Documentation Reconciliation

**Expected:** operator docs, design, execution record, config/CALM contract, and changelog fragment
all state the same default-zero behavior and honest evidence boundary.

- [x] **Step 1: Reconcile the customization registry and telemetry design with delivered semantics.**

- [x] **Step 2: Reconcile this plan and the Lite README; add the per-change changelog fragment.**

- [x] **Step 3: Prove the six known obsolete documentation patterns are absent.**

- [x] **Step 4: Run focused contract, config, architecture, dataflow, docs-version, changelog, and diff checks.**

  Required commands and limits:

  ```powershell
  node core/meetings/contracts/alloy-stt-telemetry.v1/validate.mjs --check
  node scripts/gates.mjs contract-version
  node scripts/gates.mjs config-contract
  node scripts/arch-dsl.mjs --check
  node scripts/gates.mjs dataflow
  node scripts/gates.mjs docs-version
  node scripts/changelog-collect.mjs --check
  git diff --check
  ```

  Small checks stop at 45 seconds; gates stop at 60 seconds. Changelog collection is expected to
  exit `3` while the new fragment is pending and must name that fragment; any other result stops
  the task.

---

### Task 9: Independent Review and Real Acceptance

**Expected:** completion is claimed only after independent review, both disposable-Redis lanes, a
clean source-derived image, runtime provenance, and bounded real Google Meet evidence.

- [ ] **Step 1: Request independent read-only review of the frozen integrated diff.**

  Review spec coverage, real scheduler/slot instrumentation, counter balance, publisher isolation,
  strict ownership, server aggregation, generation-local promise reuse, restart fencing, opt-in
  rollback, and unrelated-change absence. Record Critical, Important, Minor, and open questions
  with exact file/line evidence.

- [ ] **Step 2: Close both explicit disposable-Redis lanes and rerun only affected focused boundaries.**

  Do not proceed with an ownership, schema, TTL, cleanup, or error-isolation mismatch.

- [ ] **Step 3: Build one clean Dockerfile image from the integrated source and prove runtime/source provenance.**

  Stop the correlated build process tree at 15 minutes, retain the last active stage, and do not
  repeat an unchanged stalled build. A healthy old container is not evidence for the new source.

- [ ] **Step 4: Run one bounded real Google Meet journey: join, audio, Whisper, Redis, API, and Terminal.**

  Use product endpoints, not Docker health self-report, as the acceptance evidence. Stop at
  15 minutes on transcript/telemetry stall and retain one aligned API, Redis, bot-log, and Terminal
  snapshot.

- [ ] **Step 5: Verify real Russian, English, and code-switch transcription without a pinned language.**

  For bundled local STT, start the test backend with:

  ```powershell
  make -C deploy/lite up LOCAL_STT=1 WHISPER_MODEL=Systran/faster-whisper-small
  ```

  `WHISPER_MODEL` is a make variable that selects a multilingual backend model, not an ALLOY flag
  or request-language pin. Record the configured backend/model and actual transcript output; do
  not infer multilingual acceptance from configuration or omission of the language parameter.

- [ ] **Step 6: Measure queue, lag, RTF, supersede, and recovery behavior under bounded real load.**

  Confirm active/waiting return to zero and that `STT unavailable` is never interpreted as an empty
  queue.

- [ ] **Step 7: Record final evidence and resolve every Critical or Important review finding through named RED/GREEN.**

  Include exact commands/durations, pass/fail counts, Redis URL class without credentials, native
  meeting id, key/TTL evidence, observed metric maxima, recovery behavior, image provenance,
  source/index state, external mutations, and remaining limitations.

## Permitted final states

- `complete`
- `implementation complete / live evidence blocked_external`
- `in progress`

Do not claim clean-image success, matching runtime, Redis integration PASS, real Meet E2E,
multilingual acceptance, load recovery, pilot completion, or production readiness from focused
source and contract checks alone.
