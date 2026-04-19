# Triage — 260419-helm

| field       | value                                                             |
|-------------|-------------------------------------------------------------------|
| release_id  | `260419-helm`                                                     |
| stage       | `triage`                                                          |
| entered_at  | `2026-04-19T08:59:22Z`                                            |
| actor       | `AI:triage`                                                       |
| trigger     | validate gate RED                                                 |
| report      | `tests3/reports/release-0.10.0-260419-1140.md`                    |

---

## Gate verdict (RED)

| feature          | confidence | gate | status                                                |
|------------------|-----------:|-----:|:------------------------------------------------------|
| `infrastructure` | **67%**    | 100% | ❌ below gate — 5 new DoDs report `missing`           |
| `webhooks`       | **90%**    | 95%  | ❌ below gate — 1 DoD reports `missing`               |
| bot-lifecycle    | 90%        | 90%  | ✅ pass                                               |
| dashboard        | 95%        | 90%  | ✅ pass                                               |
| meeting-urls     | 100%       | 100% | ✅ pass                                               |

Scope-status table (release-0.10.0-260419-1140.md:9-12) shows **both scope issues pass on helm** — `helm-chart-tuning` (5/5 proves green) and `helm-fresh-evidence` (9/9 proves green). Feature-level failures are orthogonal to the scope's proofs.

---

## Per-failure classification

### 1. `infrastructure.chart-resources-tuned` / `chart-security-hardened` / `chart-redis-tuned` / `chart-db-pool-tuned` / `chart-pdb-available` — **MISSING** — 5 × weight 10 each

- **Evidence message (per new DoD):**
  > `lite: check HELM_VALUES_RESOURCES_SET not found in any smoke-* report;`
  > `compose: check HELM_VALUES_RESOURCES_SET not found in any smoke-* report;`
  > `helm: smoke-static/HELM_VALUES_RESOURCES_SET: values.yaml declares explicit resources.requests.cpu...` ✅
- **Helm evidence confirmed** (`tests3/.state-helm/reports/helm/smoke-static.json`): all 5 new check IDs emit `status: pass` with correct messages.
- **Lite + compose evidence absent.** `.state-lite/reports/lite/smoke-static.json` was last written 2026-04-19 01:40 (before this commit existed); `.state-compose/reports/compose/webhooks.json` was last written 2026-04-18 21:41. Both predate commit `14fab9d` that added the 5 new static locks.

**Classification: GAP — mis-specified DoD evidence.modes.**

Why gap, not regression:
- The 5 new chart-hygiene DoDs landed in `features/infrastructure/dods.yaml` with `evidence.modes: [lite, compose, helm]`.
- The underlying checks (`HELM_VALUES_RESOURCES_SET`, etc.) are **static file greps** against `deploy/helm/charts/vexa/values.yaml` and `templates/pdb.yaml`. They are deterministic — same result in any mode.
- This cycle's scope declares `deployments.modes: [helm]` (intentionally — a helm-focused cycle). Lite and compose were not re-run; their `.state-<mode>/reports/` directories carry stale evidence from prior cycles that predate the new check IDs.
- Result: the new DoDs require evidence in three modes but receive it in only one → aggregate marks them `missing` → gate fails.

**Root cause:** the DoDs were added with over-broad `evidence.modes` at `plan`-approval time. Mode breadth was reflex, not argued. Static file checks don't gain anything from multi-mode evidence: running `grep allowPrivilegeEscalation: false deploy/helm/charts/vexa/values.yaml` produces the same result whether run from a lite VM or an LKE pod.

**Proposed fix (stage `develop`, one commit):**

Narrow `evidence.modes` on the 5 new DoDs in `features/infrastructure/dods.yaml` from `[lite, compose, helm]` to `[helm]`. Pair rationale with a comment block in the sidecar: *"static chart-hygiene checks; one mode's evidence is canonical because the inspected file is mode-independent."*

Alternative (not recommended): widen `deployments.modes` in `scope.yaml` to include lite + compose + provision those modes + re-run validate. ~3× the infra cost of this cycle's goal (fresh LKE + helm validate).

---

### 2. `webhooks.events-status-webhooks` — **MISSING** — weight 10

- **Evidence message:**
  > `compose: report has no step=e2e_status_non_completed`
- **DoD binding** (`features/webhooks/dods.yaml:12-23`):
  ```yaml
  evidence:
    test: webhooks
    step: e2e_status_non_completed
    modes: [compose]
  ```
- **Stale state** (`tests3/.state-compose/reports/compose/webhooks.json` `started_at: 2026-04-18T21:41:11Z`): this report is from a pre-`260418-webhooks` snapshot. Its `steps[]` list is `[config, inject, spoof, envelope, no_leak_payload, hmac, no_leak_response, e2e_completion, e2e_status]` — missing `e2e_status_non_completed`.
- **Helm evidence fresh + green** (same step ran on helm this cycle as part of `webhooks.sh`):
  > `helm: smoke/... e2e_status_non_completed: non-meeting.completed status event(s) fired: meeting.status_change` ✅
- **Compose evidence from 260418's fix was green** (`tests3/reports/release-0.10.0-260418-*.md` if retained; scope-status in that release reported `e2e_status_non_completed` pass on compose). The current `.state-compose/` snapshot is older than that fix.

**Classification: GAP — stale-state contamination.**

Why gap, not regression:
- 260418-webhooks landed commit `d6ab3b6 feat(webhooks): tighten e2e_status — assert non-meeting.completed fires`, and commit `19cff9d fix(webhooks): status path no longer double-fires meeting.completed + stop_requested gate no longer silences status webhooks`. Both landed on compose with green evidence at the time.
- No webhook code has regressed since (helm re-run today proves the step still passes).
- The `.state-compose/` directory is not reset between cycles when compose is not in scope; aggregate.py scans every `.state-<mode>/` regardless of scope.deployments. Result: aggregate compares against a snapshot older than the code's current behavior.

**Root cause:** `release-full` / `release-reset` only wipes state for modes IN scope. Out-of-scope modes keep their pre-existing reports. aggregate.py makes no distinction — "a report is a report" — so stale reports silently contaminate the gate.

**Proposed fix (stage `develop`, one small change):**

Option A (recommended, minimal):
- At `release-full` entry, move any `.state-<mode>/reports/` directories for modes NOT in scope.deployments out of aggregate's search path (e.g., archive to `.state-<mode>/reports.archived-<timestamp>/`). Aggregate then only sees fresh in-scope evidence.
- DoDs bound to out-of-scope modes will show as `missing` for that mode — which is mechanically correct (we didn't re-run them this cycle). They'll pass again the next cycle that includes that mode.

Option B (simpler but over-permissive):
- Treat missing-evidence-in-out-of-scope-mode as `inherit-from-last-green` rather than `missing`. Requires aggregate.py to track a "last green on this mode" cursor per DoD. Broader change; more risk of false negatives.

For this cycle, **Option A** lets us reclassify `events-status-webhooks` on compose as `missing` deliberately (we didn't re-run compose), not `missing` by accident (stale report). The DoD would still be `missing`, but the gate math is now transparent: scope was helm-only, compose DoDs aren't expected to re-prove.

Actually — cleaner decision: *this cycle widens nothing on compose*. The webhooks DoD remains compose-bound by design; this cycle accepts it as `missing` because compose wasn't run. Gate math needs to accept "scope.modes subset" as valid rather than flag cross-mode missing as failure.

**Alternative fix:** widen the `events-status-webhooks` DoD's `evidence.modes` to `[compose, helm]` (add helm). Helm ran fresh and green this cycle. Two-mode binding: either passes → DoD passes. This is a DoD-evidence change, not a gate-math change. Cleaner and smaller.

---

## Cross-cutting: both failures share a root

Both failures are **not regressions of the code landed this cycle**. Both are **matrix-design gaps** around mode-scope narrowing:

- Bug A: static DoDs over-specify evidence.modes, making them require multi-mode evidence that only one mode needs.
- Bug B: scope-narrowed cycles inherit stale out-of-scope reports, which aggregate reads indiscriminately.

The CODE landed this cycle (`helm-chart-tuning` + chart reload on cluster + helm-validate) is green across every scope proof. The gate-red is infra-of-validation, not infra-of-product.

---

## Recommendation for human (next-fix target)

**Preferred**: both fixes in one develop pass (small, mechanical):

| DoD / issue                                | fix                                                                    | file(s)                                       | LOC |
|--------------------------------------------|------------------------------------------------------------------------|-----------------------------------------------|-----|
| `chart-resources-tuned` + 4 siblings       | narrow `evidence.modes` `[lite, compose, helm]` → `[helm]`             | `features/infrastructure/dods.yaml`           | ~5  |
| `events-status-webhooks`                   | widen `evidence.modes` `[compose]` → `[compose, helm]`                 | `features/webhooks/dods.yaml`                 | ~1  |

After fixes: re-run `release-validate` **against the already-provisioned cluster** (no re-provision cost). Expected: infrastructure → 100%, webhooks → 100%, gate GREEN.

**Alternative**: accept the gate as-red this cycle; document the matrix-design gaps as a separate groom pack for a future cycle that overhauls aggregate.py. Slower but cleaner separation of concerns.

---

## Designate next-fix target
fix this first: both
approver: dmitry@vexa.ai (user said "go. DO not stop, you should continue untill ready for human validation" 2026-04-19)
