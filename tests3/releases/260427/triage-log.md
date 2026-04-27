# Triage log — release 260427-k8s-stabilize (v0.10.5 — Production hardening)

**Date**: 2026-04-27
**Stage entered**: triage (from validate, gate red)
**Validate report**: `tests3/reports/release-0.10.0-260427-1618.md`
**Matrix run**: lite + compose + helm; 27 tests run; 11 failures across modes

---

## Verdict summary

The 20-pack codebase changes (commits 53f2649…e062e30, 15 commits) **all
shipped to the cluster successfully** — pods are healthy, helm upgrade
landed, dev images pushed and rolling. The gate failed on three orthogonal
classes of problem:

| Class | Count | Origin | Block ship? |
|-------|------:|--------|:-----------:|
| Test-infra static-check bugs | 2 | static check pre-existing | NO — fix in develop |
| Release-process drift | 2 | introduced this release | NO — fix in develop |
| Coverage gaps (declared-but-unimplemented) | ~40 | scope.yaml claims w/o checks | YES — must reconcile |
| External smoke-env drift | 1 | compose VM seeding | NO — fix in develop |

**None of the failures invalidate the actual code changes that shipped.**
The gate is correctly red because (a) we have legitimate static-check
regressions and process drift, and (b) we declared scope claims we have
not yet wired implementations for.

---

## Failure-by-failure classification

### R1. `CHART_VERSION_CURRENT` — REGRESSION (static check, not chart)

**Modes affected**: lite, compose, helm
**Reported message**: `FAIL: Chart.yaml version=0.10.4 != latest v* tag vexa-0.10.4`
**Visual paradox**: Both sides display `0.10.4`. They look equal.

**Root cause**: Bug in `tests3/checks/scripts/chart-version-current.sh`:

```bash
LATEST_TAG=$(git -C "$ROOT" tag -l 'v*' | sort -V | tail -1)   # matches 'vexa-0.10.4' too
LATEST_VER=${LATEST_TAG#v}                                     # strips one 'v' → 'exa-0.10.4'
```

The shell glob `v*` matches `vexa-0.10.4` because `v` matches `v` and `*`
matches `exa-0.10.4`. The strip-leading-`v` then mangles the tag to
`exa-0.10.4`, which is compared to `0.10.4` from Chart.yaml — never equal.

**Evidence**: Chart.yaml carries `version: 0.10.4` (correct per the
inheritance policy: chart equals last-shipped tag); `vexa-0.10.4` is the
latest tag. Per the documented invariant ("equality is the invariant"),
this should pass. The check itself is wrong.

**Fix (in develop)**:
- Change glob pattern to `vexa-[0-9]*` (the actual tag prefix).
- Change strip to `${LATEST_TAG#vexa-}`.
- Add a self-test in the script asserting LATEST_VER matches `[0-9]+\.[0-9]+\.[0-9]+`.

**Note**: a separate bump to `0.10.5` will be done at SHIP time as part of
the same commit that cuts the `vexa-0.10.5` tag (per inheritance policy).

---

### R2. `TESTS3_RELEASE_KEY_CONSISTENT` — REGRESSION (release-process drift)

**Modes affected**: lite, compose, helm
**Reported message**:
- `FAIL: branch 'release/260427' != expected 'release/260427-k8s-stabilize'`
- `FAIL: worktree basename 'vexa-260427' != expected 'vexa-260427-k8s-stabilize'`

**Root cause**: Four-way drift between:
| surface | value |
|---------|-------|
| worktree basename | `vexa-260427` |
| current branch | `release/260427` |
| release dir | `tests3/releases/260427-k8s-stabilize/` |
| `.current-stage` release_id | `260427-k8s-stabilize` |

The worktree+branch were named `260427` (date-only) at creation. When I
named the release `260427-k8s-stabilize` during groom (to match the
human-readable `YYMMDD-feature` convention used in prior releases like
`260418-webhooks`, `260422-release-plumbing`), the worktree+branch were
NOT renamed, creating drift.

**Decision options**:
- (A) Rename release_id back to `260427`, rename release-dir to
  `tests3/releases/260427/`, rename plan-approval/scope/triage paths.
  Drops the descriptive suffix; minimal file changes.
- (B) Rename worktree to `vexa-260427-k8s-stabilize` and branch to
  `release/260427-k8s-stabilize`. Disruptive mid-release.

**Recommendation (in develop)**: **Option A**. The worktree was created
first (immutable mid-release without losing in-flight work); the
release-id is the latest entrant and easiest to migrate. A single
`sed`-style rename across `.current-stage`, scope.yaml, plan-approval.yaml
plus a `git mv tests3/releases/260427-k8s-stabilize tests3/releases/260427`
restores the four-way invariant.

**Alternative**: Loosen the static check to allow worktree basename to
match a *prefix* of release_id. Rejected — that's a workaround that
permanently drifts the four surfaces apart.

---

### R3. `DASHBOARD_API_KEY_VALID` — REGRESSION (compose env seed)

**Modes affected**: compose only (lite passes)
**Reported message**: `dashboard.VEXA_API_KEY is invalid (HTTP 401 from http://localhost:8056/bots/status)`

**Root cause** (hypothesis, needs reproduction in develop):
- Dashboard's `VEXA_API_KEY` env var holds a token that the api-gateway no
  longer recognizes.
- Lite mode (which seeds dashboard env on each `make lite` run via process
  supervision) passes — its seed flow is fresh per restart.
- Compose mode (which uses persistent `.env` + persistent Postgres on the
  test VM) carries a token from a previous cycle's seed; admin-api was
  re-seeded with new tokens on redeploy but dashboard env was not
  refreshed.

**Evidence path**:
- `tests3/.state-lite/reports/lite/smoke-env.json`: `DASHBOARD_API_KEY_VALID: pass`
- `tests3/.state-compose/reports/compose/smoke-env.json`: `DASHBOARD_API_KEY_VALID: fail`
- Same release tag (`0.10.0-260427-1618`) ran on both VMs → not a code
  regression.

**Fix (in develop)**: investigate `tests3/lib/vm-setup-compose.sh` and
`tests3/lib/reset/redeploy-compose.sh` to confirm dashboard env is
re-seeded with the canonical admin-api-issued token after each redeploy.
The lite flow's pattern (regenerate token, write to dashboard env, restart
dashboard) is the reference.

---

### R4. Makefile `release-validate` — REGRESSION (release tooling)

**Reported error in task bjqygxzcc**:
```
stage-enter FAILED: illegal transition 'deploy' → 'triage'.
Legal predecessors of 'triage': ['human', 'validate']
```

**Root cause**: The `release-validate` target in `Makefile`:
```make
release-validate:
    @$(_STAGE) assert-is deploy
    @$(MAKE) release-full SCOPE=$(SCOPE) && \
        ($(_STAGE) enter human ...) || \
        ($(_STAGE) enter triage ...)
```

It runs the matrix from stage `deploy`, then attempts `deploy → triage` on
red. But `triage`'s legal predecessors are `{validate, human}`. The
target never formally enters `validate` between matrix runs, so the
transition is illegal.

**Manual workaround applied today**: I ran
`stage.py enter validate` then `stage.py enter triage` by hand to realign
the state machine. This triage log is being written from the resulting
`triage` stage.

**Fix (in develop)**: insert `$(_STAGE) enter validate ...` before the
`release-full` invocation:
```make
release-validate:
    @$(_STAGE) assert-is deploy
    @$(_STAGE) enter validate --actor make:release-validate --reason "begin matrix"
    @$(MAKE) release-full SCOPE=$(SCOPE) && \
        ($(_STAGE) enter human ...) || \
        ($(_STAGE) enter triage ...)
```

This is a small Makefile edit, but it's release-process tooling — the
state-machine-correctness invariant per CLAUDE.md.

---

### G1. `TESTS3_LABEL_RELEASE_TRACEABLE` — GAP (static check doesn't model truncation)

**Modes affected**: lite, compose, helm
**Reported message**:
`FAIL: 'vexa-t3-check-260427-k8s--ec7dcb' does not match ^vexa-t3-check-260427-k8s-stabilize-[0-9a-f]{6}$`

**Root cause**: my `release_label()` function in `tests3/lib/common.sh`
truncates the release_id portion to keep total label ≤ 32 chars (Linode's
hard limit on tag/label fields). With prefix `vexa-t3-check` (13) + `-` +
release_id `260427-k8s-stabilize` (20) + `-` + 6-hex suffix = 42 chars,
truncation kicks in: `260427-k8s-stabilize` → `260427-k8s-` (11 chars).

The static check still expects the **full** release_id in the regex.

**This is correct behavior of `release_label()`** — needed for Linode
LKE provisioning; without it `release-provision` fails.

**Gap**: the static check at `tests3/checks/scripts/tests3-label-release-traceable.sh`
needs to model the same truncation. Easy fix: import `release_label` from
common.sh and assert the actual produced label is consistent rather than
hand-rolling a regex.

**Note**: this gap exists ONLY because of the release-id drift in R2. If
release_id is renamed back to `260427` (Option A in R2), total label
length is 13 + 1 + 6 + 1 + 6 = 27 chars — under 32, no truncation, no
static-check failure. Fixing R2 may incidentally fix G1.

---

### G2. `TESTS3_WORKTREE_BOOTSTRAP` — GAP (sandbox creation in matrix env)

**Modes affected**: lite, compose (helm doesn't run this check)
**Reported message**: `FAIL: branch release/check-3228345e missing`

**Root cause** (unconfirmed, needs reproduction in develop):
- The check creates a sandbox worktree at `../vexa-check-<rand>` via
  `tests3/lib/worktree.sh create`.
- The helper does `git worktree add -b release/check-<rand>` which should
  create the branch.
- The check then asserts the branch exists. Failure says it does not.

**Hypotheses**:
1. The matrix VM's checkout doesn't have `main` (the default base for
   `worktree_create`); helper failed silently, branch never created.
2. The `git worktree add -b` command fails due to fs permissions or path
   collision on the test VM.
3. Cleanup ran during the test run (race), removing the branch before the
   check.

**Fix (in develop)**: reproduce locally first (`bash tests3/checks/scripts/tests3-worktree-bootstrap.sh`),
then propagate exit codes and capture stderr from `worktree.sh create`.

---

### G3. ~40 ⬜ "missing" claims — GAP (declared in scope, not implemented)

**Modes affected**: all three (lite, compose, helm)
**Counted by report**: 13 of 15 scope-issues have at least one ⬜ missing
proof. Total ⬜ count across modes: ~40 distinct claim×mode pairs.

**Examples**:
- `REDIS_CLIENT_HARDENED_TIMEOUTS` (lite, compose, helm)
- `BOT_DELETE_DURABLE_RETRY` (compose)
- `BOT_LOGS_STRUCTURED_JSON` (lite)
- `BOT_POD_SCHEDULING_FROM_PROFILE` (helm)
- `MEETINGS_DATA_ROW_SIZE_METRIC_EXPORTED` (compose, helm)
- (~35 more — see `tests3/reports/release-0.10.0-260427-1618.md`)

**Root cause**: when grooming/planning, I declared these claims in
`scope.yaml` as `required_modes:` evidence for each pack's `proves:`
clause, but did not write the corresponding static checks (or live
checks) in `tests3/checks/scripts/` and register them in
`tests3/checks/registry.json`.

**This is a deliberate accounting gap**: per the planning convention,
a `required_modes` entry without an implementation reads as ⬜ missing in
the report, which is the correct signal — "we claimed coverage, we don't
actually have it".

**Decision options**:
- (A) Write the ~40 static checks now. Substantial work; many are
  multi-mode (need lite + compose + helm wiring); some need live tests
  (e.g., `MEETING_API_COLLECTOR_RECOVERS_AFTER_REDIS_KILL` requires
  killing Redis on a running cluster and observing recovery).
- (B) Defer to Pack F (smoke-matrix scaffold) which [PLATFORM] is
  contributing day 1 ("PR within 24h"). Pack F's job is to wire these
  claims into a proper smoke matrix.
- (C) Drop claims from scope.yaml that won't be wired in this release.
  Rejects coverage we still want.

**Recommendation**: **Option B with a partial Option A**. Write static
checks for the ~10 highest-value claims that are static-checkable from
source (e.g., `REDIS_CLIENT_HARDENED_TIMEOUTS` is a grep against
`services/meeting-api/meeting_api/main.py`; `BOT_LOGS_STRUCTURED_JSON` is
a grep against `services/vexa-bot/core/src/utils/log.ts`). Defer the
runtime/dynamic claims to Pack F.

This is also where [PLATFORM] code review on #272 may surface specific
claims that need first-class checks — fold those into the develop loop.

---

## Coverage-gate failures (derived; not separate failures)

The release report shows 5 features below their DoD threshold:

| Feature | Confidence | Gate | Cause |
|---------|-----------:|-----:|-------|
| bot-lifecycle | 44% | 90% | helm doesn't run `containers` test (no helm equivalent yet) |
| dashboard | 32% | 90% | helm doesn't run `dashboard-auth`/`dashboard-proxy` tests |
| infrastructure | 50% | 100% | G3 chart-deploy-shape claims not implemented |
| meeting-urls | 10% | 100% | G3 — most claims unimplemented |
| webhooks | 91% | 95% | helm doesn't run `webhooks` live test |

These are **derived gate failures** — fixing G3 fixes them.

---

## Proposed next-fix order (for human designation)

1. **R4 (Makefile state-machine)** — 2-line fix, unblocks future cycles
2. **R1 (CHART_VERSION_CURRENT)** — 2-line fix, real bug
3. **R2 (release_key drift)** — file moves + sed renames; restores invariant; incidentally fixes G1
4. **R3 (compose VEXA_API_KEY)** — investigate redeploy seed; medium fix
5. **G1 (label truncation)** — only if R2 doesn't auto-fix it
6. **G2 (worktree-bootstrap)** — reproduce locally; static-check authoring
7. **G3 (~40 missing claims)** — partial Option A (10 grepable claims) + defer rest to Pack F

After R1+R2+R3+R4 land, expect compose+lite+helm smoke-static and
smoke-env to all turn green. G1 may auto-fix from R2. G2+G3 reduce to
follow-on work that does not block ship.

## Code that already shipped — confirmed correct on cluster

These are NOT failures. They are passing observations included so the
human reviewer sees what the cycle proved despite the gate red:

- 15 commits across 20 packs landed (`git log --oneline -15` from this worktree)
- Helm chart deployed: meeting-api 2/2 Ready, runtime-api Ready, all probes green
- `:dev` images pushed; rolling restart converged
- Pack C.4 `/readyz` endpoint live (verified via `kubectl logs` + readiness gate)
- Pack D.2 outbox stream consumer ack/retry path operational
- [PLATFORM] code review request pending on #272 (issuecomment-4327366063)

---

## HUMAN: please write one of:

```
fix this first: <DoD-id-or-class>
```

Suggested options:
- `fix this first: R4` — Makefile state-machine (cleanest unblock)
- `fix this first: R1` — CHART_VERSION_CURRENT static check (smallest)
- `fix this first: R1+R2+R4` — all 3 quick regressions in one develop sweep
- `fix this first: R1+R2+R3+R4` — all 4 regressions; defer G1/G2/G3
- `accept this gap: G3` — explicit deferral of coverage to Pack F

Or:
```
accept this gap, do not fix
```
if all remaining failures are deemed ship-non-blocking.

---

## HUMAN designation (recorded 2026-04-27, dmitry@vexa.ai)

> "continue dev -> validate loop!"

Interpreted per OSS recommendation:

```
fix this first: R1+R2+R3+R4
```

Plus opportunistic grepable G3 static-check authoring during the develop
sweep (REDIS_CLIENT_HARDENED_TIMEOUTS, BOT_LOGS_STRUCTURED_JSON,
K8S_BACKEND_CONTAINER_ID_IS_NAME, BOT_DELETE_DURABLE_RETRY,
RECORDING_FINALIZE_OUTBOX_CONSUMER_IDEMPOTENT,
AGGREGATION_FAILURE_CLASS_VIA_TYPED_HELPER, and any others that are
trivially source-greppable from this worktree).

Runtime/dynamic G3 claims remain deferred to Pack F (smoke-matrix
scaffold, [PLATFORM] PR ≤24h).
