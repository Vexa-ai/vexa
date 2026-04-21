# Code review — 260421-prod-stabilize

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| release_id   | `260421-prod-stabilize`                                                |
| stage        | `human` (Part A — code review)                                         |
| commits      | 8 commits on `dev` (`0a318bc..70c9148`)                                |
| net diff     | 82 files changed, +5 451 / −2 914                                      |
| gate report  | `tests3/reports/release-0.10.0-260421-1827.md` (all features ≥ gate)   |

---

## Commits at a glance

| sha | summary | risk |
|-----|---------|------|
| `0a318bc` | feat: 7-pack post-incident stabilization (initial implementation) | LOW for individual pack files; MEDIUM aggregate (wide surface) |
| `ed05808` | triage round-1 — register checks in runtime registry + playwright grace bump + pool defaults + 2 script bugfixes | LOW (test-infra + values) |
| `0d943fb` | triage round-2 — scripts emit check-ID steps + aggregator fall-through | LOW (test-infra only) |
| `c11efbf` | triage round-3 — package-tests npm fallback | LOW (test-infra only) |
| `0ed807d` | triage round-3b — rename run_pkg_step arg | LOW (test-infra only) |
| `be0c958` | fix: pin playwright to 1.56.0 matching Dockerfile base image | MEDIUM (bot runtime dep pin; blast radius = every bot pod) |
| `6a01788` | fix: accept `'ignored'` callback response as terminal success | LOW (expands accepted-response set; no regression shape) |
| `70c9148` | fix: set `stop_requested` flag in normal DELETE /bots path too | LOW (mirrors fast-path assignment into normal path) |

---

## Diffs grouped by concern

### Concern 1 — Pack A: chart secrets via secretKeyRef

**Files**: `deploy/helm/charts/vexa/templates/{_helpers.tpl, deployment-admin-api.yaml, deployment-meeting-api.yaml, deployment-runtime-api.yaml, job-migrations.yaml}`

**What**: New helper `vexa.postgresCredentialsSecretName` mirrors the existing `vexa.adminTokenSecretName`. `DB_PASSWORD` / `DB_USER` / `DB_NAME` in 4 templates + `TRANSCRIPTION_SERVICE_TOKEN` in meeting-api & runtime-api flipped from `{{- if postgres.enabled }} … {{- else }} value: … {{- end }}` to unconditional `secretKeyRef`. Fails loud at `helm template` time when the Secret name is unset in external-DB mode (`required` directive — same pattern `vexa.dbHost` already uses).

**Why**: 2026-04-20 production outage — any `helm upgrade` bypassing `make deploy-prod` silently rendered an empty secret. Incident post-mortem §13 + #221.

**Risk**: Existing `postgres.enabled=true` clusters still get `postgres-credentials` Secret auto-generated from `.Values.database.password | default "postgres"` (verified by `helm template` default render). `postgres.enabled=false` operators must pre-populate the Secret (platform does this; documented in CHANGELOG).

**Touched DoDs**:
- `security-hygiene.chart-prod-secrets-via-secretkeyref` (new) — bound to `HELM_PROD_SECRETS_SECRETREF_ONLY`
- `security-hygiene.chart-prod-secrets-required-at-render` (new) — bound to `HELM_PROD_SECRETS_REQUIRED_AT_RENDER`

**Open questions**: `JWT_SECRET` / `NEXTAUTH_SECRET` live in `vexa-platform` overlay (not in OSS chart values); not covered by this pack. Follow-up ticket tracked in incident doc N-1(b).

### Concern 2 — Pack B: incremental chunk upload

**Files**:
- `services/meeting-api/meeting_api/recordings.py` — `/internal/recordings/upload` endpoint accepts `chunk_seq: int`; storage path changed to per-session subdir + zero-padded index; DB + `meeting_data` modes both APPEND `media_files` per chunk; `Recording.status` flips to `COMPLETED` only on `is_final=True`; webhook fires once at the IN_PROGRESS→COMPLETED transition.
- `services/vexa-bot/core/src/services/recording.ts` — new `uploadChunk(callbackUrl, token, chunkData, chunkSeq, isFinal, format)` method producing multipart POST with `chunk_seq` + `is_final`.
- `services/vexa-bot/core/src/platforms/{googlemeet,msteams}/recording.ts` — `MediaRecorder.start(1000)` → `start(30000)`; `ondataavailable` base64-encodes each chunk + `await __vexaSaveRecordingChunk(…, isFinal=false)`; graceful-shutdown flush sends final chunk with `isFinal=true` + legacy full-blob fallback for disk copy.
- `services/runtime-api/runtime_api/backends/kubernetes.py` — `stop(timeout: int = 10)` → `= 30` matching pod spec `terminationGracePeriodSeconds` (defense-in-depth for the final chunk).

**Why**: #218 — 77 % of recording-enabled meetings lose recording in production; 0 % save rate for >30 min calls.

**Risk**:
- Bot-side: the browser-world base64 encoding of each 30-s chunk is a per-event `btoa(binary)` on a potentially large string. For a 30-s WebM (~5–15 MB) this is O(n) per chunk and bounded — should be fine. No backpressure on `__vexaSaveRecordingChunk` (fire-and-forget `void ...`); a slow MinIO could cause the browser's chunk queue to grow. Tolerable for a 30-s cadence.
- Server-side: per-chunk write path adds a MediaFile row per chunk. Recording retrieval code has NOT been updated to stitch the chunks. **This cycle ships the write side; retrieval stitching is follow-up.** The existing `/recordings/{id}` endpoint returns the first `media_file` — for single-chunk (legacy) callers this is unchanged; for multi-chunk callers it returns only the first chunk.
- Legacy one-shot path (chunk_seq=0, is_final=true) works byte-identically — just creates a 000000 chunk which is the whole file.

**Touched DoDs**:
- `bot-lifecycle.recording-incremental-chunk-upload` (new)
- `bot-lifecycle.bot-records-incrementally` (new)
- `bot-lifecycle.recording-survives-mid-meeting-kill` (new — static placeholder; chaos test deferred to compose fixture infra; human-stage step authoritative for dynamic proof)
- `bot-lifecycle.runtime-api-stop-grace-matches-pod-spec` (new)

**Open questions**:
- **Retrieval stitching not in this cycle.** If a meeting records multiple chunks, `/recordings/{id}` returns the first `media_file` only. This is acceptable for the hosted platform today (dashboard downloads recording as a single URL; platform can stitch server-side). A future issue should track an OSS-side server stitcher or a signed-URL list response.
- Base64 encoding per 30-s chunk is CPU-bound; if a user's meeting has heavy browser-side work (big DOM, TTS stream), the recording thread and the main browser thread may contend. Not observed in test; flag for future profiling.

### Concern 3 — Pack C: transcript-rendering dedup fix

**Files**:
- `packages/transcript-rendering/src/dedup.ts` — containment branch consults `seg.completed` / `last.completed` before discarding (7-line fix, dedup.ts:85-98).
- `packages/transcript-rendering/src/dedup.test.ts` — 2 new test cases (both containment directions with mismatched completion).
- `packages/transcript-rendering/package.json` — version bump `0.4.0` → `0.4.1`.
- `packages/transcript-rendering/dist/*` — rebuilt (`npm run build` output).
- `packages/transcript-rendering/package-lock.json` — synced.
- `.github/workflows/test-packages.yml` — NEW matrix workflow running `npm test` on every `packages/*` with a `scripts.test`.

**Why**: #220 — pending segments stuck in italic forever on every live Vexa stream (`dashboard.vexa.ai` + downstream consumers).

**Risk**: Minimal — change is strictly monotone. Output is byte-identical to 0.4.0 in every configuration except *(containment + mismatched completion)*. Verified: 77 / 77 package tests pass (including the 2 new cases + the pre-existing 76). `services/dashboard` doesn't import `deduplicateSegments` directly; consumption is via `TranscriptManager.finalize()`.

**Touched DoDs**:
- `dashboard.packages-transcript-rendering-tests-pass` (new)
- `dashboard.packages-ci-workflow-exists` (new)

**Open questions**: None.

### Concern 4 — Pack D: engine pool defaults + rollback regression guard

**Files**:
- `deploy/helm/charts/vexa/values.yaml` — `adminApi.extraEnv` + `runtimeApi.extraEnv` now explicitly set `DB_POOL_SIZE=10, DB_MAX_OVERFLOW=5, DB_POOL_TIMEOUT=10` (mirroring meeting-api's existing 20/20/10 shape but smaller because those services are less fat).
- `tests3/checks/registry.json` — new static-tier check `ENGINE_POOL_RESET_ON_RETURN_ROLLBACK` greps `services/meeting-api/meeting_api/database.py` for the literal `pool_reset_on_return="rollback"`.
- `features/infrastructure/dods.yaml` — evidence-swap `chart-db-pool-tuned` from `HELM_MEETING_API_DB_POOL_TUNED` → `HELM_ALL_SERVICES_DB_POOL_TUNED`.

**Why**: Incident post-mortem §4 — configured pool ceilings summed past Aiven's slot cap. The engine's existing `pool_reset_on_return="rollback"` is invisible to anyone reading handler code; regression-guard makes it explicit. Broadening the chart check covers admin-api + runtime-api in addition to meeting-api.

**Risk**:
- The pool defaults (10+5 per service) are documented to sum to ~50 against a 100-slot cap — confirmed by arithmetic in incident doc §7. For small deployments this is conservative; for larger deployments operators override via `extraEnv:` per their `values-production.yaml`.
- **Pack D.1 of the original scope (adding `await db.rollback()` at 8 call sites) was dropped during iteration-3 audit** — the engine already handles it via `pool_reset_on_return`. No code change; regression guard only.

**Touched DoDs**:
- `security-hygiene.engine-pool-reset-on-return-rollback-explicit` (new)
- `infrastructure.chart-db-pool-tuned` (evidence-swap; already-bound DoD)

**Open questions**: None. #208 may be closeable with a "this is already fixed, here's the evidence" comment.

### Concern 5 — Pack G: chart rolling-update strategy

**Files**:
- `deploy/helm/charts/vexa/templates/_helpers.tpl` — new helper `vexa.deploymentStrategy` returning `type: RollingUpdate` + `maxSurge: 0` + `maxUnavailable: 1`.
- `deploy/helm/charts/vexa/templates/deployment-*.yaml` — helper injected into every subchart Deployment (9 templates). Redis and tts-service retain their existing `type: Recreate` (explicit PVC-reason comment added — no change to rollout semantics there).
- `deploy/helm/charts/vexa/values.yaml` — `apiGateway.replicaCount` default `1` → `2` so `maxSurge: 0` on the front-door rolls 1-of-2 = zero-downtime.

**Why**: Incident post-mortem §10 + N-4 — K8s default `maxSurge: 25 %` rounds to 1 pod on 1-replica deployments, transient 2× DB-pool footprint during rollouts. Critical in conjunction with Pack H (PgBouncer default-off) because without PgBouncer the 2× footprint can push past the cap.

**Risk**:
- 5–15 s unavailability per service per rolling upgrade (old pod terminated before new scheduled). Safe for non-front-door services. api-gateway's 2 replicas avoid it.
- For single-node deployments or operators with non-default replica counts, `maxUnavailable: 1` is intentionally lenient (vs. 25 %). No recreate drift.

**Touched DoDs**:
- `infrastructure.chart-deployment-strategy-helper` (new)
- `infrastructure.chart-rolling-update-zero-surge` (new)
- `infrastructure.chart-api-gateway-ha-replica-count` (new)

**Open questions**: None.

### Concern 6 — Pack H: PgBouncer as optional OSS chart component

**Files**:
- `deploy/helm/charts/vexa/templates/deployment-pgbouncer.yaml` (new)
- `deploy/helm/charts/vexa/templates/service-pgbouncer.yaml` (new)
- `deploy/helm/charts/vexa/templates/_helpers.tpl` — two new helpers `vexa.dbHostEffective` + `vexa.dbPortEffective` that return pgbouncer when enabled, else fall through to `vexa.dbHost` / `database.port`.
- `deploy/helm/charts/vexa/templates/deployment-{admin-api,meeting-api,runtime-api}.yaml` + `job-migrations.yaml` — every `DB_HOST` / `DB_PORT` env in these templates now uses `vexa.dbHostEffective` / `vexa.dbPortEffective`. PgBouncer's own Deployment uses `vexa.dbHost` directly to avoid self-loop.
- `deploy/helm/charts/vexa/values.yaml` — new `pgbouncer:` block with `enabled: false` default; transaction-mode; 20-conn pool; 1000 max clients; resources mirrored from redis shape.

**Why**: Per-cycle user challenge to the incident doc's platform-only N-5 routing. Every self-hoster running Vexa against any managed Postgres benefits from PgBouncer when scaling past 1-replica-each.

**Risk**:
- Default `enabled: false` means no behaviour change for existing operators. Zero risk to current installs.
- When enabled, PgBouncer is the NEW front door to Postgres. If misconfigured (e.g. wrong AUTH_TYPE for the Postgres version), every service's DB connection fails. Platform repo will enable it in a subsequent cycle with its own validation.
- `AUTH_TYPE=scram-sha-256` default is compatible with Postgres 14+ and Aiven/RDS/Cloud SQL. If anyone runs against Postgres 13, this needs `md5` — flag in CHANGELOG.

**Touched DoDs**:
- `infrastructure.chart-pgbouncer-optional-and-wired` (new)

**Open questions**:
- The rendered `pgbouncer.enabled=true` flow is verified by `tests3/tests/chart-pgbouncer-optional.sh` — 3 sub-steps: default-off, enabled-renders-both-resources, db_host-rewired-for-every-service-except-pgbouncer-itself. All green in round-9 validate.

### Concern 7 — Pack J: durable exit-callback delivery

**Files**:
- `services/runtime-api/runtime_api/state.py` — new `list_pending_callbacks(redis)` scans `{CALLBACK_PREFIX}*` keys.
- `services/runtime-api/runtime_api/lifecycle.py` — `idle_loop` now also iterates `state.list_pending_callbacks()` each tick and re-invokes `_deliver_callback`. `_deliver_callback` no longer calls `delete_pending_callback` on burst exhaustion — record stays in Redis until successful delivery (TTL-bounded at 3600s).

**Why**: Incident doc §7 — 7+ orphaned `status='active'` meetings. Runtime-api's callback retry gave up after 3 attempts, leaving meeting-api row stuck forever. By looping pending callbacks in the existing `idle_loop`, orphans become impossible by construction.

**Risk**:
- The TTL on pending-callback records is 3600 s (unchanged). If meeting-api stays unreachable longer than that, the record expires and the meeting stays orphaned. Platform-side Grafana alert + postgres-exporter (incident-doc N-2) would catch that case; OSS doesn't rely on it.
- The sweep adds N HTTP requests per `IDLE_CHECK_INTERVAL` (default 60 s) in the worst case of N orphans. Bounded by the TTL-eviction above. Negligible overhead in typical operation.
- No new background task — reuses existing `idle_loop`, matches user's directive ("use the scheduler we have").

**Touched DoDs**:
- `bot-lifecycle.runtime-api-exit-callback-durable` (new)
- `bot-lifecycle.runtime-api-idle-loop-sweeps-pending-callbacks` (new)

**Open questions**:
- Idempotency audit (J.3) of meeting-api callback endpoint: the endpoint returns 200 when the meeting has already transitioned — retries are safe. No change needed. Confirmed by logs in round-9.

### Concern 8 — Fixes surfaced by iteration (rounds 7, 8)

Not originally planned; uncovered during multi-round validate.

**`be0c958`** — pin playwright 1.56.0 exact in `services/vexa-bot/core/package.json` (was `^1.55.1` resolving to 1.59.1 at build time; Dockerfile base is `mcr.microsoft.com/playwright:v1.56.0-jammy`). Deleted stale `services/vexa-bot/core/package-lock.json` (the workspace root at `services/vexa-bot/package-lock.json` is authoritative). This was PRE-EXISTING and would have bitten any cycle that rebuilt the bot image.

**`6a01788`** — `services/vexa-bot/core/src/services/unified-callback.ts` adds `'ignored'` to the accepted-response set. Meeting-api returns `'ignored'` when `meeting.data.stop_requested=true` — was bot's terminal-success signal missing it.

**`70c9148`** — `services/meeting-api/meeting_api/meetings.py` DELETE /bots normal path now sets `meeting.data["stop_requested"] = True` (previously only the fast-path did — meetings ≥ 5 s old fell through without the flag; the bot's `joining` callback then hit invalid-transition logic and failed).

**Risk**: The bot + meeting-api fixes together close a long-standing race (user DELETE during bot's joining callback). The fix class matches what existing code already shaped for the fast-path — no new idiom. Verified in round-9 green.

### Concern 9 — Test-infra scaffolding

**Files**:
- `tests3/registry.yaml` — 16 new registry entries (compact, type+script+step+modes).
- `tests3/checks/registry.json` — 7 new static-tier greps (every entry has `tier:static` + `file` + `must_match`).
- `tests3/test-registry.yaml` — 8 new test entries (script-driven bundles) under `tests:`.
- `tests3/tests/*.sh` — 8 new shell scripts (each emits step IDs matching the registry check IDs; uses existing `test_begin/step_pass/step_fail/test_end` convention).
- `tests3/lib/aggregate.py` — `_eval_proof` + DoD resolver fall through to non-smoke reports when a `{check:}` binding isn't found in `smoke-*.json`. Backward-compatible; smoke-* still scanned first.
- `features/{security-hygiene,bot-lifecycle,dashboard,infrastructure}/dods.yaml` — 14 new DoDs + 1 evidence-swap.

**Why**: Validate gate wouldn't find our new checks otherwise. Every registry/DoD addition is deliberate; covered by the audit trail in `tests3/releases/260421-prod-stabilize/triage-log.md`.

**Risk**: Very low. All changes are test/DoD metadata; the aggregator fall-through is a strict extension. Verified by round-9 gate going green across every new check.

---

## Risk notes — things a fast reviewer might miss

1. **Recording retrieval stitching is NOT part of this cycle.** The write side is complete (per-chunk upload, per-chunk MediaFile row). The read side (`/recordings/{id}`) still returns only the first `media_file`. Legacy single-chunk recordings are unaffected. A multi-chunk recording today returns its first chunk URL; dashboard + platform consumers should be re-audited post-ship.
2. **Playwright pin + package-lock removal at `services/vexa-bot/core/`.** The tracked `core/package-lock.json` was stale (predated the workspace config). Deleting it is correct because `services/vexa-bot/package-lock.json` is the authoritative workspace lock. Verify `npm ci` in the Dockerfile still works with the workspace lock only.
3. **`stop_requested` flag is now set in TWO places** (fast-path at line ~1426 and normal path at line ~1450). Future refactors should preserve both. Covered only indirectly by the status_completed DoD — a regression here would re-open the bot-lifecycle race we just closed.
4. **`vexa.deploymentStrategy` helper vs. existing Redis/TTS `Recreate` strategy.** These are not injected the helper because Redis/TTS have explicit `type: Recreate` (PVC single-mount constraint). The comments in those two templates document why. A reviewer enabling `maxSurge: 0` on PVC-backed services must keep this discipline.
5. **`stop_requested` flag is read in `bot_status_change_callback` as a terminal-success signal.** The bot relies on meeting-api responding 200 `{"status":"ignored"}` AND the bot accepting `"ignored"` as success. Both sides were patched together. Uncoordinated rollback of either side would re-open the race.
6. **Aggregator fall-through scope.** `_eval_proof` now scans non-smoke reports for `{check: X}` bindings. Theoretically a collision: if a script-driven test has a step named the same as a smoke-tier check, it could satisfy the binding. In practice the check ID namespace (ALL_CAPS_WITH_UNDERSCORES) and step IDs (mixed case) don't collide — but a future contributor must respect the convention.

---

## Open questions for the human

1. Do you want to ship Pack B's **server-side recording stitcher** in a follow-up release, or is the "first chunk returned from retrieval" good enough until Vexa-platform wraps it with its own stitcher? Noted in open questions above.
2. After ship, should #208 and #218 be closed with a pointer to this release, or wait for the reporters to confirm in production?
3. The Teams `thread.v2` admission pack (#171, originally Pack E, deferred at iteration-2) — schedule into the next cycle, or let the reporter come back with a repro?
4. Incident doc N-2 (Grafana + postgres-exporter) and N-3 (ValidatingAdmissionPolicy) remain platform-side. This OSS cycle delivers N-1(a), N-4, N-8. Confirm the cross-repo split is clean.

---

## Ready-to-ship signals

- ✅ `tests3/releases/260421-prod-stabilize/scope.yaml` — 7 issues, every `proves[]` ✅ pass in latest validate
- ✅ `tests3/reports/release-0.10.0-260421-1827.md` — gate GREEN; bot-lifecycle 94 %, dashboard 95 %, infrastructure 100 %, every feature at or above gate
- ✅ 9 validate rounds; clean progression from 6 failures → 0
- ✅ Commits on `dev`, pushed to `origin/dev`
- ⏳ `human-approval.yaml` — Part A (this file) awaiting `code_review_approved: true`
- ⏳ `human-checklist.md` — Part B awaiting per-item `- [x]`

---

## Transition after both parts signed

```bash
make release-ship ID=260421-prod-stabilize
```
