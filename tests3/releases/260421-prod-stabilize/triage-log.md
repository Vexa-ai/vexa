# Triage — 260421-prod-stabilize

| field      | value                                                              |
|------------|--------------------------------------------------------------------|
| release_id | `260421-prod-stabilize`                                            |
| stage      | `triage`                                                           |
| entered    | 2026-04-21T12:37Z                                                  |
| trigger    | validate gate RED — 6 features below threshold                    |
| report     | `tests3/reports/release-0.10.0-260421-1502.md`                     |

## Gate verdict (RED)

| feature | conf | gate | status |
|---|--:|--:|:--|
| bot-lifecycle | 61% | 90% | ❌ |
| dashboard | 86% | 90% | ❌ |
| infrastructure | 72% | 100% | ❌ |
| meeting-urls | 10% | 100% | ❌ |
| security-hygiene | 42% | 95% | ❌ |
| webhooks | 91% | 95% | ❌ |

Scope table: **every scope issue** reports "⬜ missing" — every proof
binding comes back with no evidence at all.

## Root causes (three layered)

### 1. Commit never pushed → VMs ran stale code — GAP (process)

`make release-deploy` kicked off while my commit `0a318bc` was local-only.
The VM redeploy scripts (`tests3/lib/reset/redeploy-compose.sh:7`,
`.../redeploy-lite.sh`) run `git fetch origin dev && git reset --hard
origin/dev` at the top, which reset the VMs back to 4ff56dd (the pre-commit
tip). Result: the VMs' `services/`, `tests3/tests/`, and `tests3/registry.yaml`
all pre-dated my develop work.

Why automation didn't catch this: there is no pre-deploy check verifying
that the committed dev-branch tip matches the local working tree.

**Fixed**: `git push origin dev` landed `4ff56dd..0a318bc` at 2026-04-21
post-RED. Commit is now at origin.

### 2. `tests3/checks/registry.json` ≠ `tests3/registry.yaml` — REGRESSION (classification) / GAP (DoD architecture)

I added every new check ID (16 of them) to `tests3/registry.yaml`. That
file is the DoD/binding layer read by `aggregate.py` to map scope proofs
→ DoDs → registry entries. **It is not the runtime registry.**

The runtime registry is `tests3/checks/registry.json` — a separate file
read by `tests3/checks/run` (the tier-based dispatcher that `smoke-static`,
`smoke-env`, `smoke-health`, and `smoke-contract` invoke). Any check ID
that does not appear in `registry.json` will never be executed during
validate; it shows up in scope-status as "⬜ missing" (not "❌ fail"),
because there is no report entry to grade.

Evidence:
- `grep "HELM_PROD_SECRETS" tests3/checks/registry.json` → no matches
- Existing `H11_PINNED_SAFE_EVERYWHERE` appears in **both** files — that's
  how 260419-oss-security's security-hygiene checks worked.

**Classification**: mixed.
- **REGRESSION** in that groom.md Iteration 3's audit-conformance step
  missed the dual-registry convention — I checked `registry.yaml`,
  `features/*/dods.yaml`, `test-registry.yaml`, but did not verify the
  runtime dispatcher file. The audit was incomplete.
- **GAP** in the DoD architecture: nothing in the tooling enforces that
  every `registry.yaml` entry has a matching `registry.json` entry. A
  one-liner `plan`-time check could flag this before develop ships.

**Fix path (now, in develop)**:
- Add 7 pure-grep checks to `tests3/checks/registry.json` as `tier: static`
  entries (RECORDING_UPLOAD_SUPPORTS_CHUNK_SEQ, RUNTIME_API_STOP_GRACE_MATCHES_POD_SPEC,
  ENGINE_POOL_RESET_ON_RETURN_ROLLBACK, HELM_DEPLOYMENT_STRATEGY_HELPER_DEFINED,
  HELM_API_GATEWAY_REPLICA_COUNT_HA, RUNTIME_API_IDLE_LOOP_SWEEPS_PENDING_CALLBACKS,
  PACKAGES_CI_WORKFLOW_EXISTS).
- Add 8 test scripts to `tests3/test-registry.yaml` (one per script file
  in `tests3/tests/`) so `run-matrix.sh` discovers and invokes them.
- The remaining compound checks (HELM_PROD_SECRETS_*, BOT_RECORDS_INCREMENTALLY,
  RECORDING_SURVIVES_MID_MEETING_KILL, TRANSCRIPT_RENDERING_DEDUP_TESTS_PASS,
  HELM_ALL_SERVICES_DB_POOL_TUNED, HELM_ROLLING_UPDATE_ZERO_SURGE,
  HELM_PGBOUNCER_OPTIONAL_AND_WIRED, RUNTIME_API_EXIT_CALLBACK_DURABLE)
  are already emitted by the test scripts via `test_begin`/`step_pass`;
  once the scripts are registered, the aggregator picks up their steps.

### 3. Pre-existing `INTERNAL_TRANSCRIPT_REQUIRES_AUTH` + `BOT_STATUS_TRANSITIONS` fail on helm — out-of-scope noise, likely GAP (stale state + init race)

Helm smoke-contract has two pre-existing check failures:
- `BOT_STATUS_TRANSITIONS: fail` (no detail)
- `INTERNAL_TRANSCRIPT_REQUIRES_AUTH: fail` (no detail)

The cluster is actually healthy:
- admin-api + meeting-api each restarted 3× at startup (classic DB-not-ready
  init race), then settled healthy. Currently serving `/docs` 200 OK every
  request.
- Pods: all running, api-gateway has 2 replicas (Pack G verified), postgres
  StatefulSet up.

These failures are almost certainly **stale contamination + a DB-init
race** orthogonal to this release's scope. The prior triage-log for
260419-helm documented the same class (stale `.state-<mode>/reports/`).
Not a regression from my changes; don't re-open here.

**Classification**: GAP (test infra hasn't been made robust to the init
race). Track separately, do NOT expand scope.

## Next-fix target

**Fix root cause #2 first** (add entries to registry.json + test-registry.yaml).
Root cause #1 is already resolved. Root cause #3 is out of scope and noted
for a later cycle.

After the fix commits land:
- `make release-deploy SCOPE=...` — VMs pull the new scripts + registry
  entries and images rebuild with no changes (same tag would be fine, but
  the deploy will bump for safety).
- `make release-validate SCOPE=...` — re-run the matrix. Expected: all
  new scope proofs resolve to pass/fail instead of missing; gate re-scores.

## Transition

```bash
python3 tests3/lib/stage.py enter develop --reason "triage picked next fix: register new checks in runtime registry"
```
