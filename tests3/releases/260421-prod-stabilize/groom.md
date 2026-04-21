# Groom — 260421-prod-stabilize

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| release_id   | `260421-prod-stabilize` *(proposed — see "Release-ID alignment" below)*|
| stage        | `groom`                                                                |
| entered_at   | `2026-04-21T10:12:56Z`                                                 |
| actor        | `AI:groom`                                                             |
| predecessor  | `idle` (prior release `260419-oss-security`, shipped 2026-04-19 17:55Z) |
| theme (AI)   | *"0.10.x production stabilization — fix live fires reported since ship"* |

---

## Release-ID alignment

`tests3/.current-stage` still carries `release_id: 260419-oss-security` because
the most recent `stage.py enter groom` was invoked without `--release`. The
prior release fully shipped+tore-down on 2026-04-19 17:55Z; that folder is
artifact-complete and is **not** clobbered by this groom.

When the human approves at least one pack and advances:

```bash
make release-plan ID=260421-prod-stabilize
```

— the `release-plan` target passes `--release $$ID` to `stage.py enter plan`,
which realigns `.current-stage.release_id` atomically with the plan transition.
No manual edits to `.current-stage` are needed.

---

## Scope, stated plainly

0.10.0 shipped 2026-04-19 (tag `0.10.0-260419-1910`). Since then, three distinct
**live-production** reports have landed, plus one pre-existing bug directly
implicated in one of them. All are bugs in behaviour on `main` right now:

1. A helm chart foot-gun that caused a multi-hour dashboard + sign-in outage on
   2026-04-20 → 21 in a real external-Postgres deployment (#221, filed today).
2. A recording-upload loss of 77 % over 7 days in the same production — 0 % for
   meetings > 30 min. Root cause pinpointed by the reporter to one line in
   `runtime-api` (#218, filed yesterday).
3. A deduplication bug in `@vexaai/transcript-rendering@0.4.0` that leaves
   "pending" segments stuck in italic forever on any live Vexa stream (#220,
   filed yesterday). **A local fix for this is already in the working tree**
   (uncommitted: `packages/transcript-rendering/src/dedup.ts` +
   `dedup.test.ts`). Groom noted only — develop will assess.
4. A meeting-api DB connection-pool leak that showed up independently on
   2026-04-16 (#208) and directly drove the pod-roll that exposed #221 five
   days later. Not new, but now known to be a recovery-blocker for the helm
   chart bug and belongs in this cycle for that reason.

Plus one fresh addendum on a long-running Teams admission bug (#171, commented
on yesterday) which isolates the failure to the *consumer*
`teams.live.com/meet/` URL shape — corporate `thread.v2` URLs admit fine.
Adjacent in theme (production reliability) but a different feature surface;
recommended as a **deferred** pack below.

### Context update — post-incident doc

The platform-repo incident post-mortem at
`vexa-platform/docs/incidents/2026-04-21-db-pool-exhaustion.md` (read during
grooming) reframes the picture materially. Three refinements to the packs
below are driven by it:

1. **Pack A is broader than #221.** The same empty-placeholder pattern exists
   for **four** secrets in `values-production.yaml`, not just `DB_PASSWORD`:
   `meetingApi.transcriptionServiceToken` (fired at 11:50 on 2026-04-21, took
   out meeting 10522's transcription), `dashboard.env.JWT_SECRET`, and
   `dashboard.env.NEXTAUTH_SECRET` (both at-risk, not yet hit). The incident
   post-mortem N-1(a) action is exactly the OSS-side fix: patch the subchart
   templates to `secretKeyRef: …` unconditionally + wrap with `required` for
   template-time fail-loud. **Pack A expands to cover all four.**
2. **Pack D's primary driver is not the leak.** The incident post-mortem §4
   pinpoints **configured pool ceilings sum (~120) > Aiven cap (100)** as
   the failure-mode cause. #208 (leaked AsyncSessions) is a factor that grows
   occupancy over time, not the direct trigger. The OSS-side fix therefore
   must ship **both** the #208 code rollbacks **and** chart-value defaults
   that don't sum past a reasonable external-Postgres cap. See Pack D below.
3. **New Pack G — `maxSurge: 0` on subchart Deployments.** Post-mortem §10
   + N-4: K8s default `RollingUpdate` with `maxSurge=25 %` = 2× pool footprint
   during a rollout. A peak-traffic rollout of `meeting-api` + `admin-api` can
   briefly push 99/100 slots. The chart-level fix is to set
   `strategy.rollingUpdate.maxSurge: 0` on subchart Deployments and bump
   `api-gateway.replicas: 2` so `maxSurge: 0` rolls 1-of-2 with zero downtime.
   This is the cheapest, most-leveraged OSS chart change and catches every
   deploy path (not just `make deploy-prod`).

**Out-of-scope (stays platform-side, `vexa-platform` repo):**

- `ValidatingAdmissionPolicy` for empty secret envs (post-mortem N-3)
- `postgres-exporter` + Grafana slot-saturation alert (N-2)
- PgBouncer in transaction mode (N-5)
- `deploy-prod` runbook + pre-deploy-check (Layer 2 / N-9)
- Operator culture / kill-the-bypass-temptation (Layer 8)

These items are explicitly called out in the incident post-mortem as platform
concerns, and the split is clean. No double-work across repos.

This cycle is intentionally defensive: no new feature work, no migrations, no
architectural shifts. Every pack has a reporter-verified repro and (except
#171) a one-file proposed fix.

---

## Signal sources scanned

| source                                                              | count | notes                                                            |
|---------------------------------------------------------------------|------:|------------------------------------------------------------------|
| `gh issue list --state open`                                        |    48 | 4 new / freshly-active since 2026-04-19 18:00Z                   |
| `gh issue list --search "updated:>2026-04-19T18:00:00Z"`            |     4 | #221, #220, #218, #171 (new comment)                             |
| `git log e18215e..HEAD` (commits since OSS-security merge)          |    11 | includes PR #216 tuning (VAD threshold), PR #217 docs, PR #215 oss-security  |
| Uncommitted working tree                                            |     2 | `dedup.ts` + `dedup.test.ts` — candidate fix for #220            |
| Prior triage log (`tests3/releases/260419-helm/triage-log.md`)      |     — | mode-mismatch + stale-state gaps noted; orthogonal to this cycle |
| Platform incident post-mortem                                        |     1 | `vexa-platform/docs/incidents/2026-04-21-db-pool-exhaustion.md` — reshapes Packs A, D + adds Pack G |
| Discord                                                             |     — | no in-repo fetcher yet (README §4.2 marks as future work); skipped |

---

## Packs — candidates for this cycle

Ordered by production-impact urgency.

### Pack A — Helm chart empty-placeholder secret foot-gun  (**recommended: YES, P0**)

- **source**: issue [#221](https://github.com/Vexa-ai/vexa/issues/221), filed 2026-04-21 09:07Z with a full 7-revision production-outage chronology. Broadened by the platform incident post-mortem at `vexa-platform/docs/incidents/2026-04-21-db-pool-exhaustion.md` (§13 Appendix enumerates four instances of the pattern).
- **symptom**: any `helm upgrade` bypassing `make deploy-prod` (including the `--reuse-values` path when drift is already in helm state) silently renders an empty secret env into subchart Deployments. Older ReplicaSets keep serving; new ones CrashLoopBackOff. Invisible until the next pod replacement.
- **severity**: **P0 / production-outage class.** Already caused one confirmed multi-hour `dashboard.vexa.ai` + sign-in outage plus a second mid-recovery 15-minute meeting-10522 transcription outage when the same pattern fired on a *different* secret during the rollout.
- **scope covers four instances of the same pattern** (incident post-mortem §13, files `deploy/helm/charts/vexa/templates/deployment-{admin,api-gateway,meeting,runtime}-api.yaml`):
  1. `DB_PASSWORD` — fired 2026-04-20 → 21 (dashboard + sign-in outage)
  2. `TRANSCRIPTION_SERVICE_TOKEN` — fired 2026-04-21 11:50 (meeting 10522 transcription)
  3. `JWT_SECRET` — at risk, same placeholder pattern in `values-production.yaml`
  4. `NEXTAUTH_SECRET` — at risk, same placeholder pattern
- **scope shape (groom view only — plan assigns the DoDs)**:
  - For each of the four secrets above: remove the `{{- else }} value: {{ .Values.xxx | quote }} {{- end }}` branch in the relevant subchart Deployment template. Make `secretKeyRef` unconditional.
  - Guard each secret's name with `{{ required "<field> must be set via secretKeyRef" … }}` so the foot-gun fails loud at `helm template` time, not silently at pod boot time.
  - CHANGELOG entry documenting that `database.password`, `meetingApi.transcriptionServiceToken`, `dashboard.env.JWT_SECRET`, `dashboard.env.NEXTAUTH_SECRET` values are no longer consulted.
  - Upstream PR will retire the platform-side `--set …` workarounds in `vexa-platform/Makefile deploy-prod` (out of scope here; see N-1(a) in the incident doc — this cycle delivers exactly the upstream half).
  - Regression check(s): one-liner each — `helm template charts/vexa -f values-with-missing-$SECRET.yaml` exits non-zero (via `required`). Runs in the same mode-family as the existing `security-hygiene.sh` static checks.
- **estimated scope**: ~4 template files × 4 secrets × same diff shape ≈ 8 unique edits + 4 static checks. **~0.5–1 day** depending on whether `JWT_SECRET` / `NEXTAUTH_SECRET` live in the `dashboard` subchart or elsewhere (plan confirms).
- **repro confidence**: HIGH — reporter supplied 5-step minimal repro for `DB_PASSWORD`; incident doc §3 timeline + §13 appendix give the exact template snippet and values-file lines for all four.
- **owner feature(s)**: `infrastructure` (template shape) + a new DoD under `security-hygiene` for the credential-handling hygiene class.
- **why P0**: this is the only bug class in the current signal that can put the hosted deployment offline without any user action. Recovery is hand-run `helm upgrade --set …` — not safely automatable today, and the incident demonstrates it fails *during* recovery (10522 broke at 11:50 after rev-14 fixed DB_PASSWORD at 11:21).

### Pack B — Recording upload loss  (**recommended: YES, P0**)

- **source**: issue [#218](https://github.com/Vexa-ai/vexa/issues/218), filed 2026-04-20, 3 substantive comments with production evidence and root cause.
- **symptom**: 77 % of recording-enabled completed meetings in the last 7 d have no recording. Save rate: 57 % at <2 min, 50 % at 2–10 min, 14 % at 10–30 min, **0 % at >30 min**. `exit 137` (K8s SIGKILL) = 100 % loss; clean exit on >30 min = 75 % loss.
- **severity**: **P0 / feature broken.** Users who enable recording effectively don't get it for any call over a meaningful length.
- **root cause (reporter-identified)**: `services/runtime-api/runtime_api/backends/kubernetes.py:169` calls `delete_namespaced_pod(grace_period_seconds=10)`, which overrides the pod's `terminationGracePeriodSeconds=30` and gives the whole shutdown — MediaRecorder stop + ffmpeg mux + MinIO upload — 10 s. A 30-min WebM uploads in ~1500 s; SIGKILL cuts it off.
- **scope shape**:
  - **B.1** (one line): raise the `timeout` default at `kubernetes.py:169` (and sibling `:289`) from 10 to ≥90 s. Also set `terminationGracePeriodSeconds` in the pod spec to match.
  - **B.2** (adjacent, cheap): add a post-mortem logger in `runtime-api` lifecycle that captures `pod.status.containerStatuses[*].lastState.terminated.{reason,signal,message}` *before* pod deletion. Without it, exit 137 is ambiguous (`OOMKilled` vs `Error` from grace-period).
  - **B.3** (investigation, scope-optional): a separate track for the **75 % clean-exit loss on long meetings**. Reporter explicitly flags this as not-grace-period-related — leading hypothesis is silent MinIO upload failure or malformed WebM on large buffers. Needs a log-capture pass from a failing bot; fix may or may not land this cycle.
  - **B.4** (deferred to a later cycle; noted for planning): restore the 0.7–0.9 incremental-chunk upload model. Makes the shutdown path tail-only and removes grace-period dependency entirely. **Not in this cycle's scope.**
- **estimated scope**: B.1+B.2 ≈ half a day including tests. B.3 = open-ended — recommend timebox 0.5 day; if not nailed, log-only and re-groom.
- **repro confidence**: HIGH for B.1 (reporter has side-by-side working/failing status-transition traces from prod). MEDIUM for B.3.
- **owner feature(s)**: `bot-lifecycle` (recording sub-feature) + `infrastructure` (grace-period).
- **new DoD candidates (plan's job)**: `recording-uploads-survive-long-meetings` + `pod-post-mortem-captured-before-deletion`.

### Pack C — `@vexaai/transcript-rendering` dedup containment  (**recommended: YES — fix already written**)

- **source**: issue [#220](https://github.com/Vexa-ai/vexa/issues/220), filed 2026-04-20 with Node repro script and full diff.
- **symptom**: `deduplicateSegments` containment branch (`src/dedup.ts:81-85`) prefers the wider time range over the `completed: true` signal. When Vexa's ASR trims the utterance boundary tighter on confirmation (common shape), the draft stays wider → confirmed is dropped → pending segment stuck in italic forever. User-visible on `dashboard.vexa.ai` and on downstream consumers (reporter flagged DNA integration).
- **severity**: MEDIUM — never loses data server-side, but surfaces as persistent stale draft text on every live transcript view.
- **scope shape**:
  - Ship the 7-line fix that is **already in the working tree** (see `git diff packages/transcript-rendering/src/dedup.ts`): `segFullyInsideLast` and `lastFullyInsideSeg` branches each consult `seg.completed` / `last.completed` before discarding.
  - Ship the 2 test cases that are already in the working tree in `dedup.test.ts` (cover both containment directions).
  - `npm version patch` → `0.4.1`. Sync `package-lock.json`.
- **estimated scope**: the code is written. Needs: review, tests green on CI, npm publish, sync with `services/dashboard/package-lock.json`. **~0.5 day.**
- **repro confidence**: HIGH — reporter provides exact segment pair, fixture script, existing test file location.
- **owner feature(s)**: `packages/transcript-rendering` — an npm-publishable package, distinct from service features. May want a new DoD `packages/transcript-rendering/dedup-prefers-confirmed-on-containment` with evidence shape: `npm test` pass in `packages/transcript-rendering`.
- **open question for plan**: does the gate-scoring system already cover npm-package releases, or does this pack need a new evidence type / feature folder? (Groom doesn't decide.)

### Pack D — DB connection-pool hygiene  (**recommended: YES, MED**)

- **sources**: issue [#208](https://github.com/Vexa-ai/vexa/issues/208) (leaked `AsyncSession`) + incident post-mortem §4 + §7 (pool-ceiling sums, right-sizing).
- **symptoms — two connected bugs, not one**:
  - (D-code) `AsyncSession.close()` does not rollback the implicit transaction. 8 direct `async_session_local()` call sites in `meeting-api` leak connections into "idle in transaction" state. All DB-hitting endpoints return 504 after ~10 min; Redis-only endpoints fine. Reporter-identified as #208.
  - (D-config) Sum of configured pool ceilings across the seven pool-holder services was **~120** while Aiven's `max_connections = 100`. `meeting-api` alone was pool=20+overflow=20=40 per replica (reduced to 8+4=12 in the incident as rev 15). Six other services ran at SQLAlchemy / asyncpg / Prisma **framework defaults** (~15 ceiling each) — silent oversubscription. Rolling-update slot doubling (§10 of incident doc) pushes transient usage 72 → 84, close to 99/100 when two services roll at once.
- **why this cycle**: the incident post-mortem explicitly ranks this as "the failure mode"; #208 alone is secondary (leaks grow occupancy over time, but the primary driver was always-on oversubscription). Fixing Pack A without fixing Pack D leaves the same class of escalation one traffic surge or rollout away.
- **severity**: MEDIUM in isolation, HIGH in conjunction with A and G.
- **scope shape** (split into two sub-packs):
  - **D.1 — code rollback fix (reporter-supplied, #208)**: add explicit `await db.rollback()` in `finally:` for all 8 direct `async_session_local()` call sites:
    - `services/meeting-api/meeting_api/meetings.py:425, 520, 1014`
    - `services/meeting-api/meeting_api/post_meeting.py:123, 151`
    - `services/meeting-api/meeting_api/webhooks.py:184`
    - `services/meeting-api/meeting_api/collector/db_writer.py:49`
    - `services/meeting-api/meeting_api/collector/processors.py:154`
  - **D.2 — explicit sane pool defaults in the chart (N-8 of incident doc)**: set `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` env explicitly on every SQLAlchemy service's Deployment template (or subchart values), with the per-service rationale in a comment. Target: sum of configured ceilings ≤ ~50 for a 1-replica-each deployment against an Aiven-class 100-slot DB. Do not leave services at silent framework defaults.
  - **D.3 — regression check**: a script-type check that runs during compose smoke and asserts `meeting-api` active DB connection count stays bounded under load. Uses `pg_stat_activity` via the compose Postgres — shape depends on what compose exposes. Plan decides the exact assertion shape (ceiling test? leak-rate over N minutes?).
- **estimated scope**: D.1 is small (8 sites × 3-line pattern). D.2 is ~7 Deployment env patches + values comments. D.3 is the larger bit. **~1–1.5 days with tests.**
- **repro confidence**: HIGH for D.1 (reporter identified every site). HIGH for D.2 (incident doc has the exact target math). MEDIUM for D.3 (depends on how aggressively the test can drive the leak).
- **owner feature(s)**: `infrastructure` (chart pool defaults) + a new DoD class under `security-hygiene` or fresh `data-layer` feature folder for the rollback pattern. Plan decides.

### Pack G — `maxSurge: 0` on subchart Deployments + `api-gateway.replicas: 2`  (**recommended: YES, cheap**)

- **source**: platform incident post-mortem §10 + N-4.
- **symptom**: K8s default `RollingUpdate` strategy (`maxSurge=25 %, maxUnavailable=25 %`) rounds to `maxSurge: 1` on every 1-replica Deployment in the chart. During a rolling upgrade, both the old pod and the new pod run in parallel for the handoff window. **Each pod holds its own DB pool** — so the transient slot footprint of the rolled service is **2× its configured ceiling**. Stacked across services with fat default pools, a peak-traffic rollout of `meeting-api` + `admin-api` at the same time can push configured ceiling to 99/100 slots against an Aiven-class 100-cap DB. Chart-level fix; OSS-appropriate.
- **severity**: MEDIUM — "one bad helm upgrade during peak" scenario carries ~40 % recurrence probability in the incident doc's §9 quantification.
- **scope shape**:
  - Set `strategy.rollingUpdate.maxSurge: 0` on each subchart Deployment (admin-api, api-gateway, meeting-api, runtime-api, plus any sibling Deployments in the chart). Old pod terminates **before** the new one schedules. Zero pool overlap; brief 5–15 s unavailability per service per rollout.
  - Mitigate the api-gateway unavailability by defaulting `apiGateway.replicas: 2` in the chart. `maxSurge: 0` with 2 replicas rolls 1-of-2 at a time → zero platform-wide downtime window.
  - Values defaults go in `deploy/helm/charts/vexa/values.yaml` so operators inherit the safe shape automatically; existing `values-production.yaml` overrides still win.
  - Regression check: static grep on rendered chart — every subchart Deployment has `strategy.rollingUpdate.maxSurge: 0` (or equivalent `0%`); api-gateway deployment has `replicas: >= 2` by default. One-liner per check.
- **estimated scope**: ~10 lines of values + 1–2 template tweaks + 2 static checks. **~0.5 day.**
- **repro confidence**: HIGH — K8s RollingUpdate semantics are deterministic; incident doc §10 has the worked example for meeting-api.
- **owner feature(s)**: `infrastructure` (chart defaults). Possibly an additional DoD under `security-hygiene` for the "deploy-time foot-gun prevention" family, in line with Packs A/D.
- **why bundle with A+D now**: post-mortem Layer 1 ("chart templates source from secrets unconditionally") + layer "maxSurge: 0" + "explicit pool defaults" are *three* chart-level changes that together cover the incident's failure mode + recovery trap + detection latency in one shot. Shipping one without the others still leaves a well-documented escalation path.

### Pack E — Teams consumer-URL admission (**recommended: DEFER, MED**)

- **source**: issue [#171](https://github.com/Vexa-ai/vexa/issues/171), new comment 2026-04-20.
- **symptom**: Teams bot always exits with `admission_false_positive` within 10–13 s after admission when the URL is `teams.live.com/meet/<numeric>` (consumer/anonymous). Same code path admits cleanly for `teams.microsoft.com/l/meetup-join/...@thread.v2/0?context=...` (corporate, from Outlook).
- **why DEFER**: (a) a different feature surface from A/B/C/D (`bot-lifecycle/msteams` admission-detection selectors, not recording/infra/transcript); (b) the fix depends on DOM inspection on a consumer-Teams session, which is platform-specific work that does not compose neatly with the stabilization theme; (c) corporate URL works, which means the majority of hosted-Vexa Teams bookings are unaffected. **Route to a dedicated "Teams consumer-URL admission" cycle.**
- **severity**: MEDIUM (feature broken on a URL shape some users have; workable URL exists for others).
- **repro confidence**: HIGH (reporter has side-by-side success/failure with matching Vexa meeting IDs).
- **owner feature(s)**: `bot-lifecycle/msteams`.
- **deferral note**: if the human overrides and wants to bundle this, expect the cycle to roughly double in length (B and E both require bot-runtime iteration that cannot be tested from static inspection).

### Pack F — Older backlog hygiene (**recommended: DROP from this cycle**)

- **#157** (Silero VAD state drift), **#104** (Whisper repetition artifacts), **#96** (session_uid mismatch), **#182** (base schema migration), **#173** (reconciliation kills prod bots on VM deploy), **#168/#167** (voice_agent_enabled + virtualCamera defaults), **#80** (admin API Swagger curl-header header mismatch — has `status: accepted`), **#62** (APIKeyHeader name in OpenAPI).
- None of these moved since 0.10.0 shipped. Each is a legitimate issue; none is a production fire **right now**. Lumping any into a stabilization cycle risks scope-creep and DoD-mode over-reach (the exact class of failure the prior `260419-helm` triage log (`tests3/releases/260419-helm/triage-log.md`) diagnosed as "over-broad evidence.modes").
- **Route**: pick up in the next groom cycle, driven by whichever users or tests surface them.

---

## Suggested cycle shapes — human picks

### Shape 1 — Full stabilization  (**my recommendation, post-incident-doc**)

- Pack A (chart empty-placeholder pattern, 4 secrets — P0)
- Pack B.1 + B.2 (grace-period bump + post-mortem logger)
- Pack C (dedup fix — code already written locally)
- Pack D.1 + D.2 + D.3 (DB pool code fix + chart pool defaults + regression)
- Pack G (`maxSurge: 0` + api-gateway replicas: 2 — cheap, high leverage)
- **Defer** Pack B.3 (75 % clean-exit loss investigation) to a follow-on release. **Drop** Pack E, Pack F.

Total: **~3.5 days develop** + validate + human. Still fits one INNER-loop cycle (chart changes are cheap; they dominate by surface area but not complexity). Delivers exactly the OSS-side half of the incident post-mortem's N-1/N-4/N-8 action list. Platform-side work (PgBouncer, Grafana, ValidatingAdmissionPolicy) proceeds in parallel in `vexa-platform`.

### Shape 2 — Tight stabilization  (**fallback if capacity is lower**)

- Pack A + Pack C + Pack G. Smallest shape that still closes the *class* of incident-recovery trap (not just one instance of one of its layers).

Total: **~1.5 days.** Leaves recording loss and pool-hygiene unaddressed (both known P0/MED) — the next week's incident volume is the cost. Still meaningfully better than single-pack hotfixes because Packs A and G together close both halves of the "bad helm upgrade → CrashLoop + slot doubling" path.

### Shape 3 — Minimum hot-fix

- Pack A only. Smallest possible single release against the most-severe reported bug.

Total: **~0.5–1 day.** Not recommended — the incident demonstrated that Pack A alone would not have prevented the outage (the pool-saturation half would have still taken the DB offline even with the chart hardened). This shape only makes sense if a different team ships Packs D + G on a parallel schedule.

### Shape 4 — Recording-only deep-dive  (if capacity is different)

- Pack B (B.1 + B.2 + B.3) — spend the cycle investigating the 75 % clean-exit loss and land the full recording-path fix including memory / buffer instrumentation.
- Defer everything else (including P0 Pack A) to a separate one-day hotfix. **Not recommended** because Pack A is an active foot-gun for ops.

---

## Halt

`groom` stops here. `scope.yaml` is `plan`'s output.

### Human approval — 2026-04-21

| field        | value                                                                  |
|--------------|------------------------------------------------------------------------|
| approver     | dmitry@vexa.ai                                                         |
| approved_at  | 2026-04-21T10:25:00Z *(current session — user said "groom approved")* |
| signal       | User turn "groom approved" after full groom.md + incident-doc context. |
| shape picked | **Shape 1 (recommended)** — Packs A + B.1/B.2 + C + D + G             |

Blanket approval interpreted as acceptance of every line in the checklist
below. Plan will echo each pack's `approved: true` into `plan-approval.yaml`
per the stage-02 contract.

- [x] Release slug: **`260421-prod-stabilize`**. `make release-plan ID=260421-prod-stabilize` will realign `.current-stage.release_id`.
- [x] **Pack A** — helm chart empty-placeholder foot-gun, 4 secrets (`DB_PASSWORD`, `TRANSCRIPTION_SERVICE_TOKEN`, `JWT_SECRET`, `NEXTAUTH_SECRET`) → `approved: true`.
- [x] **Pack B** — scope: **B.1 + B.2** only (grace-period bump at `kubernetes.py:169,289` + post-mortem logger). B.3 (75 % clean-exit loss investigation) deferred to a follow-on release. → `approved: true`.
- [x] **Pack C** — dedup fix. In-tree working-copy diff (`packages/transcript-rendering/src/dedup.ts` + `dedup.test.ts`) is the starting point; develop re-reviews and ships. → `approved: true`.
- [x] **Pack D** — scope: **D.1 + D.2 + D.3** (rollback code fix + explicit chart pool defaults + regression). → `approved: true`.
- [x] **Pack G** — `strategy.rollingUpdate.maxSurge: 0` on subchart Deployments + `apiGateway.replicas: 2` default. → `approved: true`.
- [x] **Pack E** deferred (Teams consumer-URL admission, #171) → next groom cycle.
- [x] **Pack F** dropped (older backlog) → next groom cycle.
- [x] Cross-repo split confirmed: this OSS cycle delivers incident-doc N-1(a) + N-4 + N-8. Platform repo ships N-1(b) local override retirement, N-2 Grafana + postgres-exporter, N-3 ValidatingAdmissionPolicy, N-5 PgBouncer, N-9 runbook — tracked separately.
- [x] Cycle shape: **Shape 1** (~3.5 days develop; all Packs A/B.1-B.2/C/D/G).

### Follow-ups deferred to plan (not blocking groom→plan)

- **Required reading for plan**: `vexa-platform/docs/incidents/2026-04-21-db-pool-exhaustion.md`, especially §4 (root-cause matrix), §10 (rolling-update slot doubling), and §11 (N-1 through N-10 action list). Packs A/D/G in this groom each correspond to a specific N-item — plan's DoD wording should echo the incident-doc language so the cross-repo trail is obvious.
- Decide whether `packages/transcript-rendering` gets its own feature folder under `tests3/features/` or rides under an existing one (e.g. `realtime-transcription`). Relevant to Pack C's DoD placement.
- Decide whether Pack D's regression check owns a new `data-layer` / `database-pool` feature folder, or is a new DoD on `infrastructure`.
- Cross-check that Pack A's chart fix doesn't break the in-cluster-Postgres subchart path (`.Values.postgres.enabled=true`) — reporter asserts it's already consistent but plan should verify `required` + `credentialsSecretName` is populated in that branch too. Same for the three other secrets' in-cluster-default paths.
- Decide whether `JWT_SECRET` / `NEXTAUTH_SECRET` live in the `dashboard` subchart templates (Pack A coverage) or are only `values-*.yaml` fields that the dashboard reads directly — scope of the template edit depends on it.
- Plan's registry_changes_approved section must include at minimum: four `script`- or `grep`-type checks for Pack A (one per secret — helm-template rendering exits non-zero when the secret is empty), one `script`-type check for Pack B.1 (grace-period value on the runtime-api deployment), one `script`-type check for Pack C (npm test in `packages/transcript-rendering`), one `script`-type check for Pack D.3 (bounded DB connections under load), and two `grep`- or `script`-type checks for Pack G (`maxSurge: 0` on subchart Deployments + `apiGateway.replicas: 2` default).
- Coordinate with the platform side: this cycle delivers the upstream half of incident-doc N-1 + N-4 + N-8. Platform's N-1(b) local override becomes redundant and should be deleted once the upstream chart ships with this cycle's merge — add that to the ship stage's `follow-ups` section.

### Advancing (after human approval)

```bash
make release-plan ID=260421-prod-stabilize
```

(The `release-plan` target calls `stage.py enter plan --release 260421-prod-stabilize …` which both advances the stage and realigns the release_id atomically.)

---

## Iteration 2 — 2026-04-21 (re-groom after plan draft)

Prompted by two user signals mid-`plan`:

1. **Pack B rejected as framed.** User: *"grace-period bump is not a solution,
   the real one would be to write incrementally"*. The "90 s grace-period"
   approach is a band-aid; the pod-deletion race is structural and will
   recur on every future change that extends shutdown. #218 reporter's
   §"Suggested fix" explicitly names the real fix: *"Restore incremental
   recording persistence in the bot"* (the 0.7–0.9 model).
2. **PgBouncer positioning challenged.** User: *"isn't this an OSS side of
   separation of concerns?"*. The incident doc routed PgBouncer to
   `vexa-platform` (N-5). Re-reading: every self-hoster running Vexa
   against any managed Postgres will hit the same oversubscription class.
   PgBouncer *belongs in the OSS chart* as an optional subchart (default
   off; platform's overlay flips it on). Keeping it platform-only
   forces every adopter to reinvent it.

This iteration re-opens scope. `scope.yaml` + `plan-approval.yaml` as
drafted are preserved under git (untracked but present); they'll be
regenerated after the human picks the new shape.

### Pack B — **redesigned**: incremental recording upload  (**recommended: YES, P0, now larger**)

- **source**: [#218](https://github.com/Vexa-ai/vexa/issues/218) "Suggested fix" paragraph — reporter framed this as the correct shape; Iteration 1 de-scoped it under "B.4 deferred" and chose the grace-period patch instead.
- **why reopen**: the structural failure mode of Pack B (Iteration 1) is that the bot's entire recording lives in-memory until shutdown. The grace-period bump papers over symptoms without changing that. ANY future increase in bot shutdown cost (larger buffers, slower MinIO, a new export format) re-breaks the release. The 0.7–0.9 incremental model makes shutdown tail-only and removes the dependency on a long graceful window **entirely**.
- **scope shape**:
  - **B.1 (client)**: in `services/vexa-bot/core/src/platforms/{googlemeet,msteams,zoom}/recording.ts`: change `recorder.start(1000)` → `recorder.start(30_000)` and the `ondataavailable` handler from "push to buffer" to "upload chunk to meeting-api". Each chunk lands in MinIO under a predictable key prefix `recordings/<user_id>/<meeting_id>/<session_uid>/<chunk_seq>.webm`. (Today: `recordings/<user>/<id>/<uuid>.webm`, single object.) Current reality (groomed this turn): `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:264-270` pushes chunks to an in-window buffer that's only drained at shutdown — confirms the issue's diagnosis.
  - **B.2 (server)**: `services/meeting-api/meeting_api/recordings.py:122` already has an `/internal/recordings/upload` endpoint with an `is_final: bool` parameter. Extend it (or add a sibling `/internal/recordings/chunk`) to accept `chunk_seq` + `is_final=false` semantics, stream the chunk to MinIO without buffering in meeting-api, and register/update a `media_file` row atomically. Same-speaker chunks accumulate into one `meetings.data->'recordings'` array entry with a `chunks: [...]` sub-array, or (plan chooses) one `media_file` row per chunk tied by `session_uid`.
  - **B.3 (server-side reconciler)**: a consumer of the `meeting.completed` event (existing webhook path) that scans for `recordings/<user>/<meeting>/` prefixes with no `is_final=true` close-out and marks them `partial`. Prevents orphan chunks from accumulating in MinIO.
  - **B.4 (defense-in-depth, kept from Iteration 1)**: runtime-api's `kubernetes.py:169,289` `timeout: int = 10` is still wrong in principle — even with incremental upload, the *last chunk* + meeting-api's `media_file` INSERT need a sane grace period. Bump to 30 s (matching the pod spec's `terminationGracePeriodSeconds`), keep it 30, not 90. Much less risky since no single slow operation must fit in the window.
  - **B.5 (observability, kept from Iteration 1)**: pod `lastState.terminated` post-mortem logger before pod deletion. Still cheap, still resolves the exit-137 ambiguity. Ship it.
- **estimated scope**: **~3–4 days** (was ~0.5 d). Breakdown: bot-side MediaRecorder rewiring + upload retry/backoff ~1 d; meeting-api chunk endpoint + streaming ~1 d; reconciler ~0.5 d; tests (a compose-mode chaos test — `SIGKILL` the bot mid-third-chunk; assert chunks 1+2 survive in MinIO) ~0.5–1 d.
- **repro confidence**: HIGH — reporter pinpointed the code path, supplied working + failing traces, and named the 0.7–0.9 design. Current code paths verified this turn at googlemeet/recording.ts:270 and meeting-api/recordings.py:122.
- **owner feature(s)**: `bot-lifecycle` (recording subpath) + a small surface on `infrastructure` (chunk-endpoint contract).
- **trade-off**: Iteration 1's grace-period hack would have shipped tomorrow. This fix takes ~3× as long but eliminates the bug class, not just this instance.

### Pack H — **NEW**: PgBouncer as an optional OSS subchart  (**recommended: YES IF we're going to ~5-day cycles anyway; otherwise DEFER**)

- **source**: user challenge to the platform-only N-5 routing in the incident doc + incident-doc §4 row A ("external managed Postgres + no PgBouncer in front of it").
- **why OSS, not platform-only**:
  - Any self-hoster running Vexa against ANY managed Postgres (RDS, Cloud SQL, Aiven, Supabase, Crunchy Bridge) hits the same class of oversubscription once they scale past 1-replica-each. The problem isn't Aiven-specific.
  - The OSS chart already bundles Redis, MinIO, optional in-cluster Postgres as subcharts. A PgBouncer subchart with `enabled: false` default fits the existing shape exactly.
  - Platform-side Kubernetes operators can leave the toggle off if they prefer their cloud's managed pooler (RDS Proxy, Cloud SQL Auth Proxy). The OSS chart provides the *option*; nobody is forced into it.
  - **Most importantly**: if PgBouncer lands OSS-side, Pack D.2 (sane per-service pool defaults) becomes **less load-bearing** — per-pod pool sizes decouple from the server-side ceiling. D.2 is still good hygiene but stops being the *only* defense.
- **scope shape**:
  - Add `deploy/helm/charts/vexa/charts/pgbouncer/` subchart (or a values-driven Deployment in the main chart; plan chooses).
  - Run in **transaction mode**, `max_client_conn: 1000`, `default_pool_size: 20`.
  - `pgbouncer.enabled: false` default in `values.yaml`. When enabled, the chart rewrites every subchart's `DB_HOST` env to `pgbouncer:5432` and passes the real DB coords to the PgBouncer Deployment via its own values.
  - README section + CHANGELOG: "Recommended for any deployment against a managed external Postgres (RDS, Cloud SQL, Aiven, etc)."
  - Regression: `HELM_PGBOUNCER_OPTIONAL` — one grep that the subchart exists + a render-mode test asserting enabling the toggle rewires every service's DB_HOST.
- **estimated scope**: **~1 day** (per incident doc N-5 sizing). Subchart template + values + render-time DB_HOST rewire + tests.
- **repro confidence**: HIGH — PgBouncer in transaction mode against SQLAlchemy is well-trodden, the incident doc has the target numbers, and the values surface is small.
- **owner feature(s)**: `infrastructure` (chart topology).
- **trade-off**: adds ~1 day to the cycle. In exchange, closes the entire "app-to-DB direct connection" failure class at the source, not via downstream mitigations.

### Pack J — **NEW (small)**: durable exit-callback delivery  (**recommended: YES, cheap**)

*Re-scoped after user pushback on the first draft (a meeting-api CronJob reconciler was the wrong shape — the design should prevent orphans, not chase them).*

- **source**: incident-doc §7 ("Orphaned `active` meetings in DB (no bot): 7+ (10522, 10479, 10313, 10311, 10137, 9147, others)") + N-7b — re-diagnosed this turn against the live code.
- **why "reconciler" was wrong**: a meeting-api cronjob that polls `status='active' AND updated_at < 15 min` against runtime-api's live-pod list is a downstream reconciliation. It doesn't address *why* the DB goes stale; it just sweeps. Every sweep race, every clock skew, every partial failure is a new edge case. Also duplicates the scheduler runtime-api already has (`idle_loop` at `lifecycle.py:27`).
- **actual root cause** (found this turn at `services/runtime-api/runtime_api/lifecycle.py:113-142`):
  ```python
  async def _deliver_callback(redis, name: str) -> None:
      ...
      for attempt in range(config.CALLBACK_RETRIES):   # default: 3
          ...
          if resp.status_code < 400:
              await state.delete_pending_callback(redis, name)
              return
          ...
      logger.error(f"Callback delivery exhausted for {name} after {config.CALLBACK_RETRIES} attempts")
      # record stays in Redis, nothing re-triggers it
  ```
  When runtime-api's exit callback to meeting-api fails 3× in a row (meeting-api overloaded, network blip, or meeting-api itself CrashLoops — exactly what happened 2026-04-21 during the DB-pool incident), the pending-callback record is **left in Redis with no retriggering mechanism**. The bot pod is gone, the meeting row is stuck `active`, and no existing code path will ever transition it.
- **scope shape** (uses the scheduler we already have):
  - **J.1 — durable delivery**: remove the retry cap in `_deliver_callback`. Keep the exponential-backoff *within* one attempt-burst (capped at a few minutes), then fall through. Do NOT `delete_pending_callback` on failure — leave the record.
  - **J.2 — scheduler sweeps pending callbacks**: extend the existing `idle_loop` (same file, line 27) to also iterate `state.list_pending_callbacks()` and re-invoke `_deliver_callback(name)` every `IDLE_CHECK_INTERVAL`. Idempotent — no new task, no CronJob, reuses the one background loop.
  - **J.3 — callback-endpoint idempotency audit**: verify `services/meeting-api/meeting_api/callbacks.py` treats "already-transitioned" meetings as a no-op 200 (not a 4xx that would re-trigger retry). If it doesn't, add a single-line guard.
  - **J.4 — one-time data migration**: a single `UPDATE meetings SET status='failed', data=jsonb_set(…)` for the 7+ currently-orphaned rows the incident doc lists. Platform-side migration, not OSS chart concern — recorded here as a cross-repo follow-up; OSS cycle delivers the code that prevents new orphans from accumulating.
- **why this is "by design, not by cronjob"**:
  - Durable exit-callback delivery makes the state transition **always eventually complete** once runtime-api and meeting-api can talk. No scheduler sweeps an external symptom — the primary event source (pod exit → exit callback) becomes reliable.
  - An orphan is only possible if a bot exits AND runtime-api's Redis is simultaneously wiped before the callback delivers. That's issue #173's failure mode (VM redeploy), which is a *distinct* runtime-api state-durability bug and not in this cycle's scope.
  - Identification is no longer needed because orphans stop occurring; the 7+ existing rows are a one-time cleanup, not a perpetual problem.
- **regression check**: `RUNTIME_API_EXIT_CALLBACK_DURABLE` — compose-mode script that (a) creates a bot, (b) kills meeting-api (or blackhole its URL), (c) stops the bot, (d) restores meeting-api, (e) asserts the meeting transitions to `completed` within `IDLE_CHECK_INTERVAL + delivery_window`. Dynamic, deterministic, ~30 s.
- **estimated scope**: **~0.5 day** including regression test (same as the reconciler version, better shape).
- **owner feature(s)**: `bot-lifecycle` (container-exit → meeting-status transition is part of the bot lifecycle contract). Likely a new DoD: `exit-callback-delivery-is-durable`.
- **cross-reference**: issue [#173](https://github.com/Vexa-ai/vexa/issues/173) (reconciliation scheduler kills active production bots on VM deployment) is the *dual* failure mode — runtime-api loses Redis on redeploy, `reconcile_state()` at `lifecycle.py:145` marks live containers stopped. Both #173 and Pack J are symptoms of the same underlying issue: runtime-api's state durability. Pack J fixes the exit-callback half (cheap, small). #173 fixes the state-persistence half (larger, separate cycle). **Defer #173.**

### Pack D — **revised** in light of Pack H

If Pack H (PgBouncer) ships in this cycle:

- **D.1 (code rollbacks) — still YES.** The AsyncSession leak is real and independent of pool topology.
- **D.2 (chart-level explicit pool defaults)** — becomes **nice-to-have, not essential**. PgBouncer caps the real upstream connection count; per-service SQLAlchemy pools become local concerns. Reduce the scope from "set defaults on every service" to just "set them on meeting-api" (where the reporter's 20+20=40 was already known fat). Saves ~half a day. Or drop entirely — plan decides.
- **D.3 (dynamic pool-exhaustion regression)** — still YES. Cheap guard, flips the right way regardless of PgBouncer.

If Pack H is deferred: Pack D stays as originally approved (D.1 + D.2 + D.3 all in).

### Other issues I re-scanned and chose NOT to add

- **#173** (Reconciliation scheduler kills active production bots on VM deployment) — related to Pack J but deeper (the reconciler itself is broken; Pack J adds a new reconciler). Risky to bundle without a fresh triage. **Defer.**
- **#189** (Bot leaves when participants JOIN) + **#190** (bot doesn't auto-leave when all leave) — small bot-lifecycle bugs from external reporter. **Defer.** Different feature surface, uncorrelated to the stabilization theme.
- **#168** (voice_agent_enabled=True default streams avatars) + **#167** (initVirtualCamera unconditional) — resource-waste defaults that are cheap to fix (~0.5 d combined). Would be a sensible "while we're here" bundle IF the cycle has slack. **Defer** unless the human explicitly wants them — otherwise next groom.
- **#208** (DB pool leak) — already covered by Pack D.1.
- **#157** (Silero VAD drift), **#104** (Whisper repetitions), **#96** (session_uid), **#182**, **#62**, **#80** — out of theme. **Drop to next groom.**

### Revised shape options

#### Shape 1-v2 — **Do It Right (my new recommendation)**

Packs A + B (redesigned, full incremental upload) + C + D.1 + D.3 + G + H + J.

(D.2 dropped because H covers it structurally. D.1 and D.3 remain as belt-and-braces.)

Total: **~6–7 days develop** + validate + human. Fits one INNER-loop cycle for an experienced iterator; plan for ~9–10 wall-clock days. Closes all five reported bugs *at the root*, adds PgBouncer as a first-class OSS-chart feature, and cleans up the orphan-meeting pile as a side-effect of Pack B.5's post-mortem telemetry.

#### Shape 1-v2-lean — Drop Pack H

Packs A + B (redesigned) + C + D (full) + G + J.

Skip PgBouncer this cycle (ship it in the next). Pack D.2 stays because it's still the only in-chart defense without PgBouncer. Total: **~5–6 days**. Also valid; defers the structural DB-topology fix by one cycle.

#### Shape 2-v2 — Tight (if capacity truly forces it)

Packs A + C + G + B.5 (post-mortem logger only) + B.4 (grace-period 30 s, not 90).

Explicitly defers the real Pack B fix. Costs ~1.5 days. **Would recommend only if the next cycle is immediately scheduled for the full Pack B incremental-upload work.**

#### Shape 3-v2 — Minimum (chart-only, no app code)

Packs A + G + H only. All three are chart-file changes; they could ship as one PR without touching any service code.

Total: **~1.5 days**. Leaves recording loss, transcript dedup, and pool leak all unaddressed. **Not recommended** — fails to act on the actual user-facing regressions.

### Waiting on human (re-grooming, supersedes Iteration 1 approval)

- [ ] Confirm Pack B reopened as the incremental-upload rework (Shape 1-v2 / 1-v2-lean / 2-v2 / 3-v2 — all either include redesigned B or deliberately defer it).
- [ ] Decide Pack H: **ship this cycle (Shape 1-v2)** or **defer (Shape 1-v2-lean)**.
- [ ] Confirm Pack J (re-scoped: **durable exit-callback delivery** in runtime-api's existing `idle_loop`, not a new CronJob / reconciler) lands this cycle.
- [ ] Confirm Pack D scoping: full D.1+D.2+D.3 (if H deferred) vs slimmer D.1+D.3 (if H shipped).
- [ ] Confirm the list of "NOT adding" (#173, #189, #190, #168, #167, etc) is correct — or pull #168+#167 in if there's slack.
- [ ] Which shape: **Shape 1-v2 (recommended, biggest)** / Shape 1-v2-lean / Shape 2-v2 / Shape 3-v2.
- [ ] Release slug: keep `260421-prod-stabilize` or rename (e.g. `260421-stabilize-deep` to signal the scope expansion)?

### Halt (iteration 2)

Iteration 1's plan artifacts (`scope.yaml`, `plan-approval.yaml`) are preserved
but stale. Once shape is picked, I'll regenerate both to match. **Do not**
`make release-provision` against the current `scope.yaml` — it still reflects
the grace-period-bump Pack B.

---

## Iteration 3 — 2026-04-21 (architecture-conformance audit)

Prompted by user: *"research the features and services to make sure we follow
the existing architectural decisions OR propose updates to them so we do not
drift with things like this cronjob you proposed"*.

Systematic audit of every approved-or-proposed pack against the code it
touches. Findings reshape Pack A (smaller — existing infra covers more than
I thought), Pack B (simpler — existing endpoint supports chunking natively),
Pack D (substantially smaller — the "leak" is architecturally prevented by
`pool_reset_on_return`), Pack G (fixes a regex drift I had), Pack H (confirms
the monolithic-chart pattern, not subcharts), and Pack C (needs a small
additional GH Actions workflow).

### Chart — monolithic, not subcharted

`deploy/helm/charts/vexa/Chart.yaml` has no `dependencies:`. All optional
services (`postgres`, `redis`, `minio`) live as top-level templates in the
same chart, toggled via `.Values.<service>.enabled`. `_helpers.tpl` uses
the `{{ required "…" .Values.foo }}` pattern for external-dependency
fallbacks (see `vexa.redisHost`, `vexa.redisUrl`, `vexa.dbHost`).

**Pack H** (PgBouncer) must follow exactly this shape: add
`templates/deployment-pgbouncer.yaml` + `templates/service-pgbouncer.yaml`
guarded by `{{- if .Values.pgbouncer.enabled }}`, plus `pgbouncer:` block
in `values.yaml` mirroring `postgres:` / `redis:` (incl. resources block).
A new helper `vexa.dbHostEffective` wraps `vexa.dbHost` and returns the
pgbouncer service name when enabled. **No subchart.** No `Chart.yaml`
dependencies edit. This matches prior art byte-for-byte.

### Pack A — smaller than I thought (existing Secret infra covers half the work)

Canonical patterns already present:
- `templates/secret.yaml` auto-generates a Secret with `ADMIN_API_TOKEN`, `TRANSCRIPTION_SERVICE_TOKEN`, `INTERNAL_API_SECRET`, optional `VEXA_API_KEY` — read from `.Values.secrets.*`. Also supports BYO via `.Values.secrets.existingSecretName`.
- `templates/secret.yaml` also generates `postgres-credentials` Secret with `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` when `.Values.postgres.enabled=true`.
- Helper `vexa.adminTokenSecretName` routes every service's Secret ref through the configurable name.
- `meeting-api` deployment ALREADY reads `ADMIN_TOKEN` and `INTERNAL_API_SECRET` via `secretKeyRef` unconditionally (no `{{- if postgres.enabled }}` branch — it's the canonical pattern).

**Audit verdict:**
- `TRANSCRIPTION_SERVICE_TOKEN` is already IN the auto-generated Secret (`secret.yaml` line 12). The meeting-api deployment template reads it from `.Values.meetingApi.transcriptionServiceToken` (plain value) when it could just `secretKeyRef` from the existing Secret. Fix is a ~6-line template change, not a "create-a-new-Secret" story.
- `DB_PASSWORD` has the `{{- if postgres.enabled }}` conditional in 4 deployment templates; the `=false` branch reads plain value. Fix: wire both branches to `secretKeyRef`. For the `=false` external-DB path, require the operator to pre-populate `postgres-credentials` Secret (matches the `secrets.existingSecretName` BYO pattern we already have). Guard with `{{ required "postgres-credentials must be pre-populated when postgres.enabled=false" }}` — the SAME pattern `vexa.dbHost` already uses.
- `JWT_SECRET` / `NEXTAUTH_SECRET`: unverified this turn. Spawn quick check of dashboard deployment template at plan time before finalizing.

**Revised Pack A scope**:
- ~4 template diffs (extend admin-token pattern to `TRANSCRIPTION_SERVICE_TOKEN` + `DB_PASSWORD`)
- Extend `secret.yaml` to populate `postgres-credentials` via `.Values.database.password` lookup (or require BYO)
- Same `required` pattern as `_helpers.tpl` — no new idiom
- Estimated: **~0.5 day**, unchanged from Iteration 1

**Zero drift** — Pack A now extends an existing pattern instead of introducing one.

### Pack B — the existing endpoint already supports what we need

`services/meeting-api/meeting_api/recordings.py:122-264` already has:
- `is_final: bool = Form(default=True)` — semantics for partial uploads
- `Recording.status = "uploading" | "completed"` (DB mode) and `RecordingStatus.IN_PROGRESS | COMPLETED` (meeting_data mode)
- `media_files: [{...}]` — an ARRAY field, currently only ever populated with one element
- Idempotent session-based upsert (`existing_rec` lookup by `session_uid`)
- Webhook emission only on `is_final=true` ("recording.completed")

**Audit verdict — Pack B conforms with one small extension**:
- Add one new parameter: `chunk_seq: int = Form(default=0)`. Storage path becomes `recordings/{user_id}/{storage_id}/{session_uid}/{chunk_seq:06d}.{format}` (vs today's `recordings/{user_id}/{storage_id}/{session_uid}.{format}`)
- Each chunk inserts a new `MediaFile` row (DB mode) or appends to the `media_files: []` array (meeting_data mode)
- `is_final=false` keeps `Recording.status = IN_PROGRESS`; `is_final=true` flips to `COMPLETED` and fires the webhook.
- **No new endpoint, no new storage backend, no new schema** — the shape is already there.
- **Pack B.3 (server-side reconciler for orphan chunks) is REDUNDANT** with the existing status model. A `Recording.status = IN_PROGRESS` with no terminal transition IS the "partial" marker already; retrieval decides whether to stitch-and-serve or mark partial. **Drop B.3.**
- B.4 (grace-period bump to 30s matching pod spec) — still defense-in-depth, stays
- B.5 (pod post-mortem logger) — still in

**Revised Pack B scope**: client chunking + one new parameter on the existing endpoint + storage-path change + defense-in-depth grace bump + post-mortem logger. **~2.5 days** (was ~3–4). No Pack B.3 reconciler.

### Pack C — no existing package-CI workflow exists

`.github/workflows/` has per-service workflows (`test-admin-api.yml`,
`test-api-gateway.yml`, `test-meeting-api.yml`) and none for `packages/`.
`packages/transcript-rendering/package.json` has `scripts.test: "vitest run"`
but it's only invoked if someone runs `npm test` locally.

**Audit verdict — add a small new workflow alongside the existing ones**:
- New file `.github/workflows/test-packages.yml` matching the shape of `test-meeting-api.yml`: trigger on paths `packages/**`, run `npm ci && npm test` per package in a matrix.
- Keeps the tests3 registry check (`TRANSCRIPT_RENDERING_DEDUP_TESTS_PASS`) for the release-protocol gate.
- Two-layer safety: PR-time CI catches regressions before merge; release-protocol catches them before ship.

**Revised Pack C scope**: in-tree fix + GH Actions workflow + tests3 script check + version bump. **~0.5 day** (unchanged). Minor scope add.

### Pack D — MAJOR reframe: #208's proposed fix is redundant with existing engine config

`services/meeting-api/meeting_api/database.py:56-69`:
```python
engine = create_async_engine(
    DATABASE_URL,
    ...
    pool_size=int(os.environ.get("DB_POOL_SIZE", "20")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "20")),
    pool_timeout=int(os.environ.get("DB_POOL_TIMEOUT", "10")),
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_reset_on_return="rollback",   # ← this is key
)
```

`pool_reset_on_return="rollback"` tells SQLAlchemy's pool: **every time a
connection returns to the pool, roll back any open transaction**. This runs
on `session.close()` (which happens automatically when `async with` exits).

And every one of the 8 sites #208 enumerated uses `async with async_session_local()`:
```
$ grep -rn "async_session_local()" services/meeting-api/meeting_api/
post_meeting.py:127:        async with async_session_local() as db:
post_meeting.py:139:        async with async_session_local() as db:
... (all 8 sites use async with)
```

The database.py comment (near the pool config) explicitly acknowledges
the concern and lists the defense stack:
1. `pool_reset_on_return="rollback"` (engine-level rollback-on-return)
2. Postgres-side `idle_in_transaction_session_timeout=60000ms` (auto-kill leaked transactions — see `deploy/helm/charts/vexa/values.yaml:251`)
3. Bigger pool (20/20 vs framework default 5/5) for headroom
4. Fast fail (10s) when pool is exhausted

**Audit verdict — Pack D.1 as framed in #208 is redundant work.** Adding
`await db.rollback()` in `finally:` blocks where the context manager
already triggers engine-level rollback is a no-op. The issue is either
stale (the config was tightened AFTER the report) or was a misdiagnosis
of the incident's true cause (pool-sum > Aiven cap, not per-session leaks).

**Revised Pack D**:
- **D.1 (new, small)**: add a `grep` registry check that the engine config in `database.py` still declares `pool_reset_on_return="rollback"` — so a future refactor can't silently regress the defense. (~5 lines.)
- **D.2 (still valid, smaller)**: the EXISTING `HELM_MEETING_API_DB_POOL_TUNED` DoD/check already greps for `DB_POOL_SIZE` in `values.yaml`. Broaden the check to cover admin-api, api-gateway, runtime-api — not a rewrite, just extend. (Current check: `must_match: DB_POOL_SIZE` on `deploy/helm/charts/vexa/values.yaml`.) If Pack H ships, this matters less (PgBouncer caps real upstream conn count).
- **D.3 (still valid)**: dynamic `DB_POOL_NO_EXHAUSTION` check exists in registry; just bind to scope.
- **Estimated: ~0.5 day** (was ~1–1.5). #208 may be closeable with a "this is already fixed, here's the evidence" comment.

### Pack G — regex drift fixed

My iteration-1 registry check used `apiGateway.replicas: [2-9]`. The chart
values file uses `replicaCount` (camelCase), not `replicas`. Proof:
```
grep -n replicas deploy/helm/charts/vexa/values.yaml  →  replicaCount: 1 (in apiGateway: block)
```
That regex would have failed even against a correct chart. **Fix to `apiGateway.*replicaCount: [2-9]`** (allow inter-line keys). Also
no `strategy:` field exists in values.yaml today — Pack G's shape (add
default `strategy.rollingUpdate.maxSurge: 0`) is an ADDITION, not a
modification. Fits the chart-hygiene-DoD family (same shape as
`chart-resources-tuned`, `chart-security-hardened`, `chart-pdb-available`).
No new pattern.

### Pack J — already validated

Uses existing `idle_loop` scheduler in `services/runtime-api/runtime_api/lifecycle.py:27`.
No new scheduler, no cronjob. See Iteration 2.

### Drift report (what I would have shipped wrong without this audit)

| pack | drift | conformance after |
|------|-------|-------------------|
| A | Would have introduced new Secret when `TRANSCRIPTION_SERVICE_TOKEN` is already in the auto-generated one | Extends existing pattern |
| B.3 | Would have added a reconciler when `Recording.status = IN_PROGRESS` already signals partial | Dropped |
| D.1 | Would have added redundant `rollback()` calls — engine config already handles this | Replaced with regression guard |
| G regex | `apiGateway.replicas: 2` would have failed (real key is `replicaCount`) | Fixed |
| J | Would have added new CronJob in meeting-api duplicating runtime-api's existing `idle_loop` | Extended existing `idle_loop` |

**Total scope reduction: ~1.5–2 days** (B.3 dropped, D.1 reframed smaller,
A no larger than before). The audit saved real work and prevented shipping
idioms the next contributor would have to untangle.

### Proposed architectural updates (not just conformance)

These are changes to existing architecture the audit surfaced as worth
considering in the same cycle. Flag for human pick:

1. **Make `secretKeyRef` the universal pattern for prod secrets in the chart.** Currently `ADMIN_TOKEN` / `INTERNAL_API_SECRET` follow it; `DB_PASSWORD` / `TRANSCRIPTION_SERVICE_TOKEN` don't. The inconsistency is the foot-gun that caused the 2026-04-21 incident. Pack A already heads this way; codify it as an explicit contract in `features/infrastructure/README.md` so future secrets don't revive the plain-value path. **~5 lines of doc. No extra scope.**
2. **Retire `pool_reset_on_return` as implicit-only; add a DoD that it's explicit.** The defense against per-session transaction leaks is invisible to anyone reading request-handler code. A regression check (Pack D.1 reframed) makes it visible. Arguably also: rename `DB_POOL_SIZE=20, DB_MAX_OVERFLOW=20` defaults in `database.py` to be LOWER (say 10/5) once PgBouncer lands (Pack H); the current defaults reflect the pre-PgBouncer era. **Defer past-H.**
3. **Packages CI workflow convention.** Pack C introduces the first `.github/workflows/test-packages.yml`. Plan should decide: one workflow per package (mirrors per-service shape) or one matrix workflow covering all of `packages/`. Recommendation: matrix workflow; it's ~20 lines and scales to vexa-cli / vexa-client automatically.
4. **Deployment `strategy` in a helper, not per-template.** Pack G adds `strategy.rollingUpdate.maxSurge: 0` to `values.yaml`. Option α: reference it explicitly in each of the ~9 Deployment templates. Option β: define `vexa.deploymentStrategy` in `_helpers.tpl` and inject uniformly. β is cleaner and matches the helper-driven style of `vexa.labels`, `vexa.selectorLabels`, etc. **Recommend β; ~15 lines.**

### Waiting on human (iteration 3 — supersedes 2)

- [ ] Confirm Pack A reshape — extend existing admin-token Secret pattern, don't invent new Secret.
- [ ] Confirm Pack B simplification — drop B.3 reconciler (redundant with `Recording.status=IN_PROGRESS`).
- [ ] Confirm Pack D reframe — drop redundant `rollback()` calls, replace D.1 with a regression guard on the existing engine config.
- [ ] Confirm Pack G regex fix — `replicaCount`, not `replicas`.
- [ ] Pack H subchart shape — mirror `postgres:` / `redis:` / `minio:` (monolithic chart, not a subchart). OK?
- [ ] Architectural updates 1–4 above (doc, helpers, CI workflow shape): which to include in this cycle.
- [ ] Shape pick still open: 1-v2 / 1-v2-lean / 2-v2 / 3-v2 (net scope reduced by ~1.5 d after this audit).
- [ ] Slug pick still open: `260421-prod-stabilize` or rename.

### Halt (iteration 3)

`scope.yaml` + `plan-approval.yaml` remain stale from iteration 1. I'll
regenerate both on approval. The reduced-scope shape (D.1 smaller, B.3
dropped, A unchanged) makes the full Shape 1-v2 cycle a more plausible
~5-day commit.
