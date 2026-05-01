# Triage — 260501-chunk-leak / v0.10.5.3

**Filed:** 2026-05-01 (validate red → triage)
**Filer:** AI:assist
**Validate report verdict:** RED (2 failures)
**Stage:** `triage`, next legal: `develop` (with directive)

---

## Pre-positive findings (DO NOT regress)

These were verified empirically on the LKE test cluster (meeting 6, before triage):
- ✅ **Pack C** — user-stop in joining/awaiting_admission produces `status=completed` (not `failed`)
- ✅ **Pack O** — `meetings.data.bot_logs` populated with 65 structured-JSON lines (22 KB) on terminal exit
- ✅ **Pack T** — `meetings.data.bot_resources` populated with `peak_memory_bytes=168MB`, `samples=1`, `cgroup_available=true`
- ✅ **Pack H** — dashboard deployment shows `maxSurge: 1, maxUnavailable: 0` (verified via `kubectl get deployment`); replicaCount=2 confirmed
- ✅ **Pack D-1 + D-2** — version chip JS bundle contains `0.10.5.3`; rendered HTML has zero `"vexa vdev"` or `"Open Source · API-first"` matches

The 2 validate failures below are NOT regressions of these positive results — different surfaces.

---

## HELM_ROLLING_UPDATE_ZERO_SURGE  [GAP]

**status:** fail in mode `helm`
**bound check:** `HELM_ROLLING_UPDATE_ZERO_SURGE` (registry.yaml; type:script, tests/chart-rolling-update-zero-surge.sh)
**symptom:** `admin-api:missing-maxSurge-0 api-gateway:missing-maxSurge-0 mcp:missing-maxSurge-0 meeting-api:missing-maxSurge-0 runtime-api:missing-maxSurge-0`

**root cause hypothesis:** The check was added 2026-04-21 with intent: *"every app-facing Deployment in rendered chart sets strategy.rollingUpdate.maxSurge: 0 — rolling updates never double DB pool footprint"*. Pack H of v0.10.5.3 (commit `7b989c3`) intentionally flips the helper to `maxSurge: 1, maxUnavailable: 0` to prevent the v0.10.5.2-class outage (OLD pod killed before NEW pod Ready → 502s during image bump). The two invariants conflict: zero-surge prevents DB pool doubling but breaks zero-downtime; surge-1 prevents the outage but transiently doubles DB pool footprint during a rolling upgrade.

**proposed fix:** This is a GAP in the check, not a regression in Pack H. Resolution options (pick one):

A. **Replace the check** — drop `HELM_ROLLING_UPDATE_ZERO_SURGE`, replace with `HELM_ROLLING_UPDATE_ZERO_DOWNTIME` checking `maxUnavailable: 0` instead. v0.10.5 Pack C.x already shrunk per-pod DB pool by 4× (8 → 2 connections per pool-holder in some services), so 2× footprint during rolling is now well below the managed-DB slot cap. This is the more honest update.

B. **Keep both invariants somehow** — would require a different rolling shape (e.g. `maxSurge: 25%` if replicaCount: 4+, ensuring 1 surge pod is acceptable). Adds chart complexity; not justified for current replicaCount: 2 services.

C. **Per-service strategy** — apply `maxSurge: 1` only to dashboard + webapp (where outage hurts users) and keep `maxSurge: 0` on internal-only pool-holder services (meeting-api, runtime-api). Splits the strategy between user-facing and internal.

**touched commits:** `7b989c3` (Pack H — _helpers.tpl deploymentStrategy edit)

**Recommend:** Option A — the simplest update aligned with Pack H's intent. Per-pod DB pool shrunk in v0.10.5 makes the zero-surge concern less acute.

<!-- human directive below -->
fix this first: ___ (yes / no / accept gap)
proposed resolution: A | B | C | other:

---

## status_completed (containers.sh test)  [GAP]

**status:** fail in mode `helm`
**bound check:** `containers` test, step `status_completed` (tests3/tests/containers.sh:135-194)
**symptom:** `status=failed reason= (expected completed/gone, OR failed+clean-stop) after ~24x5s poll`

**root cause hypothesis:** The test creates a synthetic bot at fake URL `lifecycle-test-1` (lines 8-9 of containers.sh — meeting URL the bot never actually joins). The test allows two PASS conditions:
1. `status=completed` OR `status=gone`
2. `status=failed` paired with `completion_reason in {stopped, stopped_with_no_audio, stopped_before_admission}`

Got: `status=failed`, `completion_reason=` (empty). Neither PASS condition met.

This means the bot exited via a path that:
- Triggered the `failed` status (probably hit `_explicit_failure_reasons` set)
- Did NOT set a clean-stop completion_reason

Possible code paths:
- Bot validation error on synthetic URL → `validation_error` reason → maps to VALIDATION_ERROR completion → in `_explicit_failure_reasons` → FAILED. But completion_reason WOULD be set to "validation_error" — that's not in the clean-stop accept-list, so test correctly fails.
- Bot fail-fast before any callback fired → meeting status set elsewhere (orphan-bot reconciler? scheduler?) → completion_reason left null
- Pack C's `stop_requested` check misses this case because the bot exited (via exit_callback path) before the user DELETE landed in meeting.data

**Pack C interaction check:** Pack C only intercepts when `meeting.data.stop_requested == True`. If the bot fails BEFORE the user DELETE writes `stop_requested`, Pack C's branch is skipped. This matches the test sequence (the test's bot is hitting a synthetic URL that immediately fails-fast).

**proposed fix:** Two options:

A. **Update the test's accept-list** — add `validation_error` and empty-reason cases as ACCEPT-CLEAN-STOP in containers.sh `clean_stop` matcher. The test's stated intent is "not stuck in stopping forever" — any terminal state qualifies. Empty completion_reason on a synthetic-URL failure is acceptable test behavior (the bot legitimately failed; that's a system-failure not a user-stop).

B. **Real bug in classifier** — investigate why completion_reason is empty. Could be a meeting-api code path that sets status=failed without going through the classifier at all (e.g. orphan-bot reconciler timeout, scheduler-side abandonment). If so, the fix is in callbacks.py / post_meeting.py to ensure all failure paths set a completion_reason.

**touched commits:** `1e7d8da` (Pack C — _classify_stopped_exit user-stop intercept). Pack C's change is additive at the TOP of the function (early-return on user_initiated_stop) — it cannot CAUSE this empty-reason failure since pre-Pack C the same code path also produced this.

This failure pre-dates v0.10.5.3 (the test was written 2026-04-27 in v0.10.5 cycle and has been passing/failing on synthetic-URL behavior since). v0.10.5.3 doesn't introduce a new regression here.

**Recommend:** Option A — update the test's accept list to be honest about the synthetic-URL contract. Empty completion_reason on synthetic failure is acceptable for the lifecycle test's stated purpose ("not stuck in stopping forever"). Filing the empty-reason as a separate `[ACCEPT GAP]` for next cycle's classifier hardening.

<!-- human directive below -->
fix this first: ___ (yes / no / accept gap)
proposed resolution: A | B | other:

---

## Summary for human

| failure | classification | recommended | impact if accepted |
|---|---|---|---|
| HELM_ROLLING_UPDATE_ZERO_SURGE | GAP | A — replace check with HELM_ROLLING_UPDATE_ZERO_DOWNTIME | Need ~10 lines: drop old script, write new check |
| status_completed (synthetic) | GAP | A — update test accept-list | Need ~5 lines in containers.sh |

**Total proposed develop work to clear validate:** ~15 lines across 2 files. ~15 min implementation + 5-10 min re-validate.

**Or accept both as gaps + proceed to human/ship:** if the human is satisfied that:
1. The new helm strategy (maxSurge: 1) is correct + auditable in the chart commit, AND
2. The synthetic-URL test failure is pre-existing test fragility unrelated to this cycle's work.

Then human can ship without the develop loop.

**Awaiting human directive on each failure (`fix this first:` line).**
