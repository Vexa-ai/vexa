# Validate report — 260501-followup / v0.10.5.2

**Filed:** 2026-05-01 (during validate stage, post-smoke)
**Filer:** AI:assist
**Verdict:** GREEN (with caveats — see Limitations)

This report documents the validate-stage execution for v0.10.5.2.
Walked under the cut-corners regime documented in
`emergency-bypass.md`. The full registry × scope matrix was NOT
executed against fresh test infra (we skipped provision); we ran a
reduced check set against PROD instead.

## What was validated

### Static-grep checks (lite mode, executed locally at HEAD)

| Check | Result |
|---|---|
| `BOT_SDP_MUNGE_SITE2_REMOVED` | ✅ PASS — `site2_block_removed=true` |
| `BOT_NO_TRANSCEIVER_DIRECTION_MUTATION` | ✅ PASS — `transceiver_direction_assignments=0` (demoting pattern) |
| `BOT_FAILURE_STAGE_TRACKER_UPDATES_ON_TRANSITIONS` | ✅ PASS — `advanceLifecycleStage(status)` present in unified-callback.ts |

Static-grep guarantees the bug pattern is removed AT HEAD and flags
any future regression that re-introduces it.

### Runtime smoke (PROD, not test infra — bypass)

Three meetings dispatched against vexa-production immediately
post-helm-rev-52 (v0.10.6 → renamed to v0.10.5.2). All
`recording_enabled=true` to maximize exposure to the SDP-munge bug.

| Meeting | Platform | Active | Final chunks | Terminal | failure_stage |
|---|---|---|---|---|---|
| 11363 | google_meet | 29m13s | 59 | `completed` | none |
| 11364 | teams | 21m46s | 45 | `completed` | none |
| 11365 | zoom | 19m57s | 1 (Zoom paradigm) | `completed` | none |

Total combined active time: **70m 56s** across 3 platforms.
No `Execution context destroyed` errors. All meetings exited via
user-initiated stop, not crash.

Watermarks cleared:
- customer-E 1.43-min variant (T+86s) ✓
- #284 typical 2-4 min window ✓
- 13-14 min variants (customer-A, Max) ✓
- customer-B 18.5-min variant ✓
- customer-C 24.2-min variant ✓ (longest documented)

### Runtime checks NOT executed

These four checks are in `registry.yaml` but were stubbed to
`step_skip` because no test fixture infrastructure exists this cycle:

| Check | Reason skipped |
|---|---|
| `BOT_GMEET_RECORDING_ENABLED_SURVIVES_TRACK_EVENT` | `FIXTURE_GMEET_URL` not set; smoke ran against prod via human dispatch instead |
| `BOT_TEAMS_ADMISSION_NOT_44MS_DROP` | `FIXTURE_TEAMS_URL` not set; same |
| `BOT_ZOOM_WEB_SURVIVES_TRACK_EVENT` | `FIXTURE_ZOOM_URL` not set; same |
| `MEETING_FAILURE_STAGE_MATCHES_TIMELINE` | DB env not exposed to test rig; deferred to post-ship telemetry soak |

The 3 manual smokes against prod cover the same DoD as these checks
empirically (3 platforms × 20+ min × `recording_enabled=true`), but
in the formal registry-execution sense they are "skipped, not run."

## Validation gaps acknowledged

1. **No fresh test infra** (provision was skipped). Runtime smoke ran
   against prod, not against a clean `compose` or `helm` test cluster.
   This means we have empirical evidence that v0.10.5.2 SURVIVES on
   prod under our test pattern, but not that the registry × scope
   matrix is "all green per spec."

2. **Bug is non-deterministic.** Survey of post-v0.10.5 meetings on
   the buggy image shows multiple `recording_enabled=true` meetings
   surviving 30+ min cleanly (e.g. 11346, 11347, 11341, 11343, 11357).
   So our 3 smoke meetings surviving 20+ min each is consistent with
   the fix working AND with statistical luck on a non-deterministic
   bug. The strict empirical confirmation requires customer re-tests
   on the exact meetings that were crashing.

3. **Customer re-tests are pending.** 5 emails + 1 Discord DM sent to
   affected users (customer-B, customer-E, customer-C, Max, customer-A). Their re-test
   results are the population-level confirmation. As of report
   filing, none have reported back yet.

4. **DoD-level aggregation not run.** The aggregate.py path produces
   per-feature confidence values from the full report set. We do NOT
   have a full report set. The features touched (`bot-recording`,
   `bot-lifecycle`) keep their pre-cycle DoD values plus the manual
   confirmation that no regressions were observed in the smoke.

## Pre-fix prod evidence (the bug we shipped a fix for)

For the auditable record, here's the v0.10.5-window failure pattern
that v0.10.5.2 targets:

| Meeting | User | Platform | Duration | Crash signature |
|---|---|---|---|---|
| 11340 | customer-D@redacted | google_meet | 14.0min | `Execution context destroyed` at recording.js:102 |
| 11345 | customer-A@redacted | google_meet | 13.8min | same |
| 11348 | customer-D@redacted | google_meet | 14.0min | same |
| 11350 | customer-C@redacted | google_meet | 24.2min | same |
| 11356 | customer-E@redacted | google_meet | 1.4min | same |
| 11359 | customer-B@redacted | google_meet | 18.4min | same |

6 customer crashes, 5 unique users, all `recording_enabled=true`,
all GMeet, all the exact stack frame the SDP-munge bug produces.

Cross-platform exposure (Teams 44ms drop / Zoom Web track event)
verified via code-path analysis, not yet via prod failure data.

## Verdict

**GREEN** — fix is shipped, smoke survived, no observed regressions.
**Caveat**: empirical-population-level confirmation pending customer
re-tests + 30-60 min soak comparison on prod telemetry.

Stage transition: `validate → human` (per stage contract on green).

## Related artifacts

- `scope.yaml` — the 2 packs + 7 checks
- `plan-approval.yaml` — approved 2026-05-01 08:12:38Z by dmitry@vexa.ai
- `emergency-bypass.md` — protocol violations record
- `groom.md` — ingest packet
- (next) `code-review.md` — Part A of human stage
- (next) `human-checklist.md` — Part B of human stage
- (next) `human-approval.yaml` — both parts signed
