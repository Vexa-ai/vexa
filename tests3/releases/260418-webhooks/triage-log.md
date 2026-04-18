# Triage — 260418-webhooks

| field       | value                               |
|-------------|-------------------------------------|
| release_id  | `260418-webhooks`                   |
| stage       | `triage`                            |
| entered_at  | `2026-04-18T21:45:42Z`              |
| actor       | `AI:triage`                         |
| trigger     | validate gate RED                   |
| report      | `tests3/reports/release-unknown.md` |
| webhooks.json | `tests3/.state/reports/compose/webhooks.json` |

---

## Gate verdict (RED)

| feature        | confidence | gate | status |
|----------------|-----------:|-----:|:-------|
| `webhooks`     | **80%**    | 95%  | ❌ below gate — **primary failure for this scope** |
| `bot-lifecycle`| 75%        | 90%  | ❌ below gate — secondary (infra) |
| `dashboard`    | 0%         | 90%  | ❌ below gate — secondary (infra) |
| `infrastructure` | 0%       | 100% | ❌ below gate — secondary (infra) |
| `meeting-urls` | 0%         | 100% | ❌ below gate — secondary (infra) |

Only **one** failure is in-scope for this release. The others are matrix-incomplete artefacts, not regressions.

---

## Per-failure classification

### 1. `webhooks.events-status-webhooks` — **FAIL** — weight 10

- **Evidence**: `compose: webhooks/e2e_status_non_completed: only meeting.completed fired — no meeting.started / meeting.status_change / bot.failed observed`
- **Report**: `tests3/.state/reports/compose/webhooks.json`, step 10
- **DoD label**: *"Status-change webhooks for non-meeting.completed events (meeting.started / meeting.status_change / bot.failed) fire when opted-in via webhook_events"*

**Classification: GAP** (new-step-reveals-runtime-gap — this is exactly what scope predicted as hypothesis #2).

Why gap, not regression:

- No webhook-code commits since the 260417-1454 green gate (only `6694502`, `61fbaee` — dods.yaml sidecar refactors, no runtime change).
- The 260417 green gate was false-positive: the old `e2e_status` step counted `meeting.completed` as proof. The tighter step this release introduced is the first check that actually requires a non-completed event.
- So the runtime was never proven on this path; the failure is the matrix catching up, not the runtime degrading. Gap, not regression.

**Root cause candidates** (plausible, need develop to confirm):

- **(a) `stop_requested` gate drops intermediate transitions on the stop path.** `services/meeting-api/meeting_api/callbacks.py:355-357` returns "ignored" if `meeting.data.stop_requested` is set, for any non-terminal new_status. In tests3/tests/webhooks.sh the bot is created and DELETE'd before it transits `joining → awaiting_admission → active`. The DELETE fast-path in `meetings.py:1423-1437` sets `stop_requested=True` and transitions directly to `completed` (within 5s of creation). So intermediate callbacks that race in later see `stop_requested=True` and return early, never firing their status webhooks. This would explain exactly why only `meeting.completed` appears in `webhook_deliveries[]`.
- **(b) STATUS_TO_EVENT silently maps `completed → meeting.completed` inside `send_status_webhook`**, causing a double-delivery when the stop path fires both `send_status_webhook` (on COMPLETED transition) and `send_completion_webhook` (from `run_all_tasks` post-meeting). The receiver sees two `meeting.completed` webhooks and never the intermediate ones. User would perceive this as "only meeting.completed arrives". Candidate fix: exclude `"completed"` from `STATUS_TO_EVENT` so the status path never fires `meeting.completed` (leave that solely to the completion path).
- **(c) Test-environment artefact.** Bot container in compose never hits `joining` / `active` callbacks because the fake URL fails admission immediately. Against a real Google Meet URL (user's production scenario), intermediate transitions would fire. But user's production scenario also reports them missing, so (a) or (b) is more likely the root cause than (c).

**Fix target for develop**: start with **(a) the `stop_requested` gate**, then validate whether **(b) the STATUS_TO_EVENT double-fire** also needs narrowing. Both are surgical changes in `services/meeting-api/meeting_api/`.

---

### 2. `bot-lifecycle` — 75% (below 90% gate) — SECONDARY

- Individual DoDs (per report):
  - `bots-status-not-422` — missing (check not found in lite/helm smoke reports). Reason: lite/helm not provisioned in this compose-only scope.
  - `graceful-leave`, `route-collision` — missing (same reason).
  - `status-webhooks-fire` (weight 5, binds to `webhooks/e2e_status`): **passes** (e2e_status still pass, since meeting.completed satisfies the loose check).

**Classification: out-of-scope**. These DoDs expect lite + helm evidence that this scope deliberately skipped (`required_modes: [compose]`). Not a regression.

**Note**: the `status-webhooks-fire` DoD in `features/bot-lifecycle/dods.yaml:87` is still bound to the loose `e2e_status`. It passes with `meeting.completed`, mirroring the gap the webhooks DoD had. **Deferred** — tightening it is out of this scope, but worth considering for a follow-up cycle.

---

### 3. `dashboard`, `infrastructure`, `meeting-urls` — 0% — SECONDARY

- All DoDs missing.
- Reason: the compose smoke run failed early on `DASHBOARD_API_KEY_VALID` (known env bug, 2026-04-07; see `tests3/registry.yaml:DASHBOARD_API_KEY_VALID` — **unrelated** to webhooks). The `$(SMOKE_STAMP)` gate then blocked downstream test scripts (`dashboard-auth.sh`, `dashboard-proxy.sh`, `containers.sh`, etc.) from running.
- Only `webhooks.sh` was run (invoked directly via SSH to bypass the smoke gate) — which is why webhooks has real data but the others are blank.

**Classification: environmental / out-of-scope**. These zeroes aren't regressions — they're "test didn't run". Not relevant to this release's fix target.

**Note**: the `DASHBOARD_API_KEY_VALID` env issue is worth its own grooming cycle but not this one.

---

## Recommendation for human (next-fix target)

**Single fix target**: `webhooks.events-status-webhooks` (FAIL).

Candidate root cause ordered by suspicion:

| # | file / seam | hypothesis | cost |
|---|-------------|------------|------|
| a | `services/meeting-api/meeting_api/callbacks.py:355-357` (`stop_requested` early-return) | Intermediate callbacks are being silenced once DELETE sets the flag; race between bot-callback and user-DELETE | ~10 LOC |
| b | `services/meeting-api/meeting_api/webhooks.py:29-33` (`STATUS_TO_EVENT["completed"]`) | Status path double-fires meeting.completed; user only sees completion events | ~2 LOC |
| c | test-env only: bot fails pre-admission and never callbacks | false alarm in test, but user's production report says same → unlikely the whole story | 0 LOC (no fix) |

Likely develop plan:

1. Remove `"completed"` from `STATUS_TO_EVENT`. (*b*) Status path will fall through to `meeting.status_change` for the final transition, but `send_completion_webhook` still fires the `meeting.completed` payload separately. Eliminates double-delivery AND exposes whether *a* is the real issue.
2. If `e2e_status_non_completed` still fails after *b*: relax the `stop_requested` gate in `bot_status_change_callback` to still fire status webhooks for the transitions it silences (i.e. record a delivery before returning "ignored"). (*a*)
3. Re-run validate. Iterate.

**Designate next-fix target**:

```
fix this first: <designation>
```

(human: write one of the following below — the one starting with `fix this first:` is what develop will pick up)

---

fix this first: both a and b in one pass
- a: remove "completed" from STATUS_TO_EVENT in services/meeting-api/meeting_api/webhooks.py
- b: fire status webhook before returning "ignored" in stop_requested branch of bot_status_change_callback (services/meeting-api/meeting_api/callbacks.py)
approver: dmitry@vexa.ai (user said "iterate until fixed" 2026-04-19)
