# Groom — 260427 / v0.10.5 — Production hardening

> **Public release name:** `v0.10.5 — Production hardening`
> **Milestone:** https://github.com/Vexa-ai/vexa/milestone/14
> **Internal release ID:** `260427-k8s-stabilize`
> **Theme:** Robustness, not features. Zero new features in scope.

| field        | value                                                                                            |
|--------------|--------------------------------------------------------------------------------------------------|
| release_id   | `260427` *(slug assigned by `plan` — proposed: `k8s-stabilize` or `helm-stabilize`)*             |
| stage        | `groom`                                                                                          |
| entered_at   | `2026-04-27T07:45:31Z`                                                                           |
| actor        | `AI:groom`                                                                                       |
| predecessor  | `idle` (prior release `260426-zoom`, plan in flight; previous shipped: `260424-…` series)        |
| theme (user) | *"groom around blockers and enhancements to the vexa platform (k8s) like #272 and more like this"* |

---

## Scope, stated plainly

The user's signal: *"groom around blockers and enhancements to the vexa platform (k8s) like #272 and more like this"*. Everything in this groom is filtered to **k8s / Helm / runtime-api / chart shape / Redis-on-k8s reliability**. Non-platform issues (transcription quality, dashboard hygiene, bot UX features) are listed as **DEFERRED** at the bottom — not invented or padded into packs.

Two distinct production cycles converge on the same theme:

1. **`vexa-platform/staging` v0.10.4 deploy cycle (2026-04-26)** filed [#272](https://github.com/Vexa-ai/vexa/issues/272) as a 4-blocker + 2-followup bundle. Every blocker fired during one Helm-on-LKE upgrade with pool taints — i.e. the production deployment shape. Issues 2/3/4 are pure chart bugs; issue 1 is recording-durability; issue 6 is observability.
2. **Two reproduced Redis-class incidents (2026-04-21 + 2026-04-26)** documented in `vexa-platform/docs/incidents/` — single root cause (aioredis clients lack `socket_timeout` / `health_check_interval` / supervisor), filed as [#267](https://github.com/Vexa-ai/vexa/issues/267). 10.5 h silent data loss + 20 lost transcripts on the 04-26 incident alone.

These are joined by a backlog of 0.10.x-era chart and runtime-api bugs that were filed during prior staging-isolation rehearsals but never landed: bot-profile scheduling (#240, #250, #235), non-default release-name hostname hardcoding (#234), orphan-bot DELETE/lifecycle (#261, #266, #258), recording-finalize race (#268), startup ordering (#248).

**Reframing note (per user direction 2026-04-27):** the OLDER vexa-platform release notes (release-004 / "blue-stage shakedown" / blue-green cutover) are stale terminology — that operational model has been retired. The underlying chart bugs they surfaced are still real and still in scope (any deploy with a non-default release name hits #234; any tainted multi-pool cluster hits #240); the JUSTIFICATION FRAMING in this groom does not lean on blue/green. Existing GH-issue bodies still reference the old terminology because they pre-date the retirement; that's a backlog-comment hygiene item, not a scope concern.

This cycle's intent is **defensive stabilization of the OSS Helm chart and the runtime-api / meeting-api code paths Helm operators depend on**. No new features. No transcription-quality work. No bot-UX changes. The bar is: an external operator running this OSS chart on a tainted multi-pool K8s cluster, against a managed Redis/Postgres, can do a production-shape `helm upgrade` and a production-shape rolling Redis bounce **without losing data, leaking pods, or hitting an immutable-Job error**.

---

## Late scope expansion 2026-04-27 — Packs J/K/L/M/N/O added (post-[PLATFORM] lock)

After [PLATFORM] declared groom complete at 11:13Z (9-pack scope locked), project-owner reviewed and authorized 6 additional packs reframing the cycle from "k8s/Helm/platform robustness" to **"production robustness across chart + platform + bot lifecycle."**

| Pack | Source | Why now | Pending |
|------|--------|---------|---------|
| **J** Bot exit classification | #255 epic — un-deferred | Project-owner observation: many failed meetings logged as `completed` (silent class) | [PLATFORM] data on #255 |
| **K** Browser-session idle eviction | #273 (filed during groom) | Project-owner observation: idle browser pods linger past 1h timeout | [PLATFORM] data on #273 |
| **L** Slim meeting list endpoint | #263 + #264 | DoS vector at scale; principle-aligned REST list-vs-detail split | None (dashboard usage audited) |
| **M** `meetings.data` row-size monitoring | JSONB-as-NoSQL audit | Surfaces structural debt without paying it down this cycle | None (200 KB best-guess threshold) |
| **N** Bot/Server contract parity | #169 + #168 surgical fixes shipped; structural follow-on per #255 epic | Prevents next #169-class field-rename regression at PR time | None (consumer-driven contract test) |
| **O** `make all` non-interactive | #198 | Documented setup broken in CI / `nohup` | None (5-line shell guard) |

Total: **15 packs · 39 checks · 10 alerts** (was 9 packs · 28 checks · 8 alerts).

Cycle target: T+13 / T+17 no-later-than (was T+10 / T+14). Six added packs add ~5 dev-days but most parallelize (J/K/L/M/N/O have no inter-pack dependencies; sequence freely with A–I).

Migration-free across all six (verified): `meetings.status` is `String(50)` not PG ENUM, `completion_reason` is JSONB string, J/K/L/M/N/O all add code + JSONB keys + Prometheus rules — never columns or enums.

Same principle filter applies:
- ✅ Robustness, not features
- ✅ No internal-subsystem fallbacks (Pack J.4 explicitly removes the "default to completed" fallback)
- ✅ No workarounds (Pack N is structural prevention; #168/#169 surgical fixes are already in main, this is the prevent-next-regression layer)
- ✅ Auto-recovery + mandatory observability (Pack K + M alerts; Pack J typed enum extension)
- ✅ Compliance-by-construction (Pack L list endpoint no longer leaks meeting metadata to clients that don't need it)

[PLATFORM] notified via [#272 issuecomment-4326522199](https://github.com/Vexa-ai/vexa/issues/272#issuecomment-4326522199); awaiting their read on the expansion + the data on #255 + #273.

---

## Tagging convention for cross-repo dialogue

OSS-side comments on GitHub issues are prefixed `[OSS]`. Platform-side comments are prefixed `[platform]`. Same human types both, different hat. The tag makes the position lineage readable in long threads. Adopted 2026-04-27 mid-groom; retroactively applied to the three OSS replies posted earlier today (on #267, #268, #235).

---

## Live engagement log — 2026-04-27 thread updates

In-flight conversation with the platform side. Each entry: which issue, what the platform side said, what OSS replied, what scope changed in this groom.

### #267 — startup variant of the silent-hang
- **[platform] (2026-04-26 13:03Z)**: identified a third failure mode beyond the two in the body. At startup, meeting-api's `try: redis.ping() except: redis_client = None` swallows connect-refused, then the next block silently skips `xgroup_create + consume_redis_stream`. Pod stays Ready, serves HTTP, no consumer reads `transcription_segments`. Same user-visible symptom (empty transcript on stop) as the runtime variant, different root path.
- **[OSS] reply (2026-04-27, this groom)**: argued against retry-and-skip (still silent-degraded, just delayed). Position: `/readyz` gate driven by `startup_complete: bool`; flag flips True only after Redis ping succeeds AND consumer tasks are alive. Bounded-retry-then-RAISE inside the ping helper; let exhaustion propagate; K8s `restartPolicy: Always` + restart backoff is the deterministic ordering mechanism, not a try/except hiding it. Invariant: **no Ready pod ever has no working consumer.** (See [issuecomment-4325493899](https://github.com/Vexa-ai/vexa/issues/267#issuecomment-4325493899).)
- **scope change**: Pack C.4 upgraded — was "retry runtime-api startup ping"; now "readiness gate + bounded retry + remove silent-skip + optional initContainer." Applies to meeting-api primarily; runtime-api inherits.

### #268 — `_delayed_stop_finalizer` parallel finding
- **[platform] (2026-04-27 08:35Z)**: identified a second instance of the in-process-state-loss family. Meeting-api uses APScheduler in-process for the 90s post-stop force-complete. If meeting-api restarts in those 90s, the timer is gone; `data.scheduler_job_id` is persisted but no replay; meeting stuck in `stopping` forever. Production evidence: rows with `status='stopping'` for >24h.
- **[OSS] reply (2026-04-27, this groom)**: argued REMOVE the timer rather than make-it-durable. After 260421-prod-stabilize Pack J shipped durable exit-callback delivery in runtime-api's `idle_loop`, the canonical mechanism for `stopping → completed` IS the durable callback. The 90s timer is now redundant defense — two competing mechanisms, double-completion smell. Replace with a single `idle_loop` sweep in meeting-api: every `IDLE_CHECK_INTERVAL`, scan for `status='stopping' AND updated_at < now() - threshold`, force-complete + log loud. Single source of truth. (See [issuecomment-4325495528](https://github.com/Vexa-ai/vexa/issues/268#issuecomment-4325495528).)
- **scope change**: new **Pack E.3** below. Net code delta is ~neutral (remove APScheduler dep + add ~30-line sweep).
- **architectural framing**: Vexa has a recurring "in-process / in-request transient state where it must be durable" anti-pattern. Inventory of instances + their fixes is now in the [OSS] comment (table). Plan stage should treat the four instances as one coordinated audit (Pack C, D.2, E.1, E.3).
- **[platform] counter-proposal (08:35Z body, restated 08:46Z)**: instead of removing the timer, route ALL in-process timed jobs through a new generic durable-scheduler primitive (Redis ZSET keyed by fire-epoch, OR Postgres `pending_jobs` table). Startup hook polls due/overdue jobs and replays idempotently. Adds: cronjob backstop for stuck `stopping` rows ([platform] proposes shipping this in their chart this week as a temporary safety net).
- **[OSS] counter-counter (this groom, [issuecomment-4325681177](https://github.com/Vexa-ai/vexa/issues/268#issuecomment-4325681177))**: pushed back on the generic primitive *for this transition*. Rationale: there are now THREE durable mechanisms claiming `stopping → completed` (Pack-J callback + durable scheduler + cronjob); triple-redundancy is over-engineering when one canonical source of truth exists. The idle_loop sweep IS a durable mechanism — state in Postgres, survives every restart, idempotent, observable. Just transition-specific instead of generic. Generic durable-scheduler is the right shape *when* (a) no Pack-J-equivalent exists, (b) fire-time is part of SLA not just upper bound, (c) many scheduled jobs amortize the primitive's cost. None of those hold for `_delayed_stop_finalizer`. **YAGNI: no generic primitive in 260427.** If concrete second-user appears (webhook retry-backoff, billing ticks, retention expiries), spin a focused cycle then. Cronjob backstops retire on OSS merge.

### #235 — production env-propagation evidence
- **[platform] (2026-04-23 18:43Z)**: side-by-side prod vs staging confirms the silent-skip-recording bug. Prod MinIO 0 objects despite real-time transcripts flowing normally. Plus secondary symptom: meeting 10745 stuck in `stopping`. *(Original report used the now-retired "blue-color" framing; the bug is reproducible on any operator's staging vs production pair.)*
- **[OSS] reply (2026-04-27, this groom)**: acknowledged Pack B coverage (env propagation + scheduling). Cross-linked the zombie-stopping symptom to the #268 thread (same architectural family — in-process timer doesn't survive restart). One issue per fix, no double-coverage. (See [issuecomment-4325496960](https://github.com/Vexa-ai/vexa/issues/235#issuecomment-4325496960).)

### Polling cadence

Polling these threads every ~2 min for fresh `[platform]` replies. Each new substantive position triggers an [OSS] response and (if scope-affecting) a groom edit. Cycle continues until the conversation reaches a steady-state set of agreed Pack shapes.

---

## Cross-reference — platform-side input received during groom (2026-04-27)

The platform side cross-checked their session findings against the open OSS issue list. Net: most of what they would file already exists upstream. Three points of action came back:

### Already-filed coverage (their summary, mapped into this groom's packs)

| platform-side finding                                                  | already filed              | this groom's pack            |
|------------------------------------------------------------------------|----------------------------|------------------------------|
| Migrations Job lacks helm hook annotations                             | **#247** (exact match)     | Pack A.2                     |
| Audio loss on short Zoom meetings                                      | partial: #251 epic, #268; #272 issue 1 is more specific | Pack E.2          |
| Bot pod profile lacks nodeSelector + tolerations                       | **#240** + **#250**         | Pack B.1/B.2                |
| aioredis silent hang + zombie pool cascade                             | **#267** (primary)         | Pack C.1                     |
| Bot DELETE leaves orphan pods                                           | **#266**, **#261**         | Pack D.1/D.2                |
| Dashboard WS status updates lost                                        | **#269** (refs #267)       | Pack C (closes as side-effect of C.1) |
| browser-session pod stays Running                                       | **#258**                   | Pack D.3                     |
| Post-meeting transcribe timeout for long recordings                     | **#241**, **#243**         | DEFERRED (gateway-timeout family — separate surface) |
| MinIO-init Job race                                                     | **#249**                   | Pack A.3                     |
| `allow-meeting-api` NetworkPolicy missing tx-gateway ingress           | **#244**                   | Pack A.5                     |
| runtime-api no Redis retry/backoff on startup                           | **#248**                   | Pack C.4                     |

### #272 consolidation recommendation (action item, NOT in this cycle's scope)

Platform notes #272 partially duplicates #247 (migrations Job hook) and #267 (aioredis). Their proposed cleanup:

1. Comment on **#247** with platform's reproduction + workaround (`Makefile kubectl delete job` preflight).
2. **Re-title #272** to focus on its unique content (audio loss, redis tolerations regression in 0.10.4, `capacityReserve.replicas: 0` falsy-zero) — strip out issues 3 + 5 which dup #247 + #267.
3. Comment on **#267** linking #272 issue 5 as a confirming repro from a second cluster.

This is a **process action**, not engineering scope. Recommended placement: **ship-stage follow-up** of this cycle, OR done immediately by whichever side has the throughput. **No effect on Pack contents** — the unique content of #272 (Pack A.1 redis tolerations, A.4 capacityReserve, E.2 Zoom audio loss) is already in scope; the duplicate parts (Pack A.2 migrations Job hook, Pack C aioredis) are in scope via the canonical issues #247 + #267.

### Genuinely missing — three new signals to fold into this groom

1. **🟡 Dashboard auto-DELETE bot without user click (meeting 11054 class)** — not yet filed. meeting-api `status_transition` recorded `"source": "user"` + `"reason": "User requested stop"` for meeting 11054, but the user did not click stop. 4 `DELETE` calls in 5 s from the dashboard pod IP. No automatic-stop logic visible in dashboard JS audit. Possible causes: React 18 strict-mode double-fire, stale service worker, multiple tabs, `useEffect` cleanup on a hot-reload. Needs reproduction with browser DevTools.
   - **Verdict for this groom: DEFER.** Webapp/dashboard surface, not k8s/Helm/platform. Belongs in a webapp-stabilization cycle. This groom logs the signal but does NOT pull it in. **Recommendation: file a separate "needs investigation" issue with the meeting-11054 evidence + the DevTools repro guidance — ship-stage follow-up of this cycle, OR independent issue creation now.**
2. **🟡 meeting-api `post_meeting` treats 503 as "no transcripts" → marks meeting failed** — not yet filed; root cause of vexa-platform incident `2026-04-23-post-meeting-collector-503`.
   ```python
   # post_meeting.aggregate_transcription
   if response.status_code != 200:
       logger.error(f"Collector returned {response.status_code} for meeting {meeting_id}")
       return  # ← silently fails into 'meeting failed' branch
   ```
   A 503 from the gated transcription endpoint (env not set on caller, OR service unavailable) is treated identically to "transcripts not ready" — meeting marked `failed`. Should retry on 5xx with exponential backoff, OR distinguish 5xx from 4xx and not mark meeting failed on 5xx.
   - **Verdict for this groom: ADD as Pack H below.** It's a k8s/platform-relevant fix (transcription-gateway availability is a chart/deploy concern; the failure mode hits any operator running tx-gateway as a separate Deployment), it's tiny (~5 lines), and a real production incident pinpointed it. **Pack H should land in this cycle.** Issue creation is itself a follow-up step.
3. **🟢 Suggest changing OSS default `redis.stop-writes-on-bgsave-error` to `no`** — chart values currently inherit Redis's default `yes`; vexa-platform already overrides to `no`. The 2026-04-21 `redis-storage-cascade` incident's primary trigger was Redis tripping into MISCONF when its block volume failed BGSAVE, refusing all writes for 46 min. Modern Redis persistence on BGSAVE failure is best-effort, not blocking — making `no` the OSS default protects every operator without a custom values override.
   - **Verdict for this groom: FOLD into Pack C as C.5b (chart default change).** Trivial values diff; same hygiene class as C.5 (maxmemory + alert). Pack C already covers Redis hygiene; this is a one-line addition.

---

## Cross-cycle observation — why the chart is the bottleneck

Looking at `tests3/releases/` history: **260419-helm** validated Helm and surfaced ~30 mode-mismatch + stale-state gaps. **260421-prod-stabilize** delivered the `secretKeyRef` foot-gun fix (Pack A), `maxSurge: 0` (Pack G), explicit pool defaults (Pack D), durable exit-callback (Pack J), incremental recording (Pack B redesigned). **260426-zoom** is in flight on a different surface (Zoom Web first-class API).

**Every k8s-shape bug in this groom was filed AFTER 260421 shipped.** The 260421 cycle handled the bugs `vexa-platform` had hit by then. The bugs in this groom are the next layer — discovered when the same operator went one step further (different node-pool taint shape, different release name, larger scale test, different rollout pattern).

The pattern that keeps biting: **the smoke matrix doesn't exercise the production deployment shape**. Pack F below proposes closing that gap structurally so the next layer of similar bugs surfaces in CI, not in incident reports.

---

## Signal sources scanned

| source                                                                                       | count | notes                                                            |
|----------------------------------------------------------------------------------------------|------:|------------------------------------------------------------------|
| `gh issue list --state open --limit 60`                                                      |    74 | full scan; 27 are k8s/Helm/platform-tagged or platform-relevant   |
| Issue [#272](https://github.com/Vexa-ai/vexa/issues/272) (filed 2026-04-26 23:35Z)           |     1 | full body — 4 blockers + 2 followups from v0.10.4 LKE deploy      |
| Issue [#267](https://github.com/Vexa-ai/vexa/issues/267) (filed 2026-04-26 07:31Z)           |     1 | full body — Redis client robustness; layered L1–L4 fix            |
| Issue [#268](https://github.com/Vexa-ai/vexa/issues/268)                                     |     1 | full body — recording finalize JSONB race; outbox pattern         |
| `gh issue list --search "label:area:deployment"` (implicit, via metadata scan)               |    11 | #234, #235, #240, #244, #247, #248, #249, #250, #258, #261, #235  |
| `vexa-platform/docs/incidents/2026-04-21-db-pool-exhaustion.md`                              |     1 | already addressed by 260421-prod-stabilize; cross-ref only        |
| `vexa-platform/docs/incidents/2026-04-21-redis-storage-cascade.md`                           |     1 | client-pool half NOT addressed; rolls into Pack C / #267          |
| `vexa-platform/docs/incidents/2026-04-23-post-meeting-collector-503/`                        |     1 | dir; **drives Pack H (new)**                                      |
| `vexa-platform/docs/incidents/2026-04-26-meeting-api-collector-silent-hang.md`               |     1 | full read — root cause is #267                                    |
| `vexa-platform/docs/gaps.md`                                                                 |     1 | platform-side gaps tracker; cross-referenced for split            |
| `vexa-platform/STATUS.md` (2026-04-24 morning review)                                        |     1 | release-004 cutover blockers G6/G30/G33/G34/G43 — platform-side   |
| Prior triage logs (`260419-helm`, `260421-prod-stabilize`)                                   |     2 | Iteration-3 audit pattern from 260421 reused in this groom        |
| Platform-side cross-reference (received during groom)                                        |     1 | three new signals (Pack H, dashboard auto-DELETE deferral, BGSAVE default → C.5b) |
| Discord                                                                                      |     — | no in-repo fetcher (README §4.2 still future work); skipped       |

---

## Packs — candidates for this cycle

Ordered by production-impact urgency. Every pack ties back to a reporter-verified incident or a reporter-supplied repro.

### Pack A — Helm chart shape bugs from #272 + adjacent  (**recommended: YES, P0, mostly mechanical**)

- **sources**:
  - [#272](https://github.com/Vexa-ai/vexa/issues/272) issues 2 (redis/pgbouncer/migrations tolerations dropped in 0.10.4), 3 (migrations Job missing helm hook — duplicates #247), 4 (capacityReserve `replicas: 0` falsy-zero)
  - [#247](https://github.com/Vexa-ai/vexa/issues/247) (migrations Job missing hook annotations — canonical issue; #272#3 is duplicate)
  - [#249](https://github.com/Vexa-ai/vexa/issues/249) (minio-init Job's 5 s sleep races MinIO PVC startup; success → no buckets created)
  - [#244](https://github.com/Vexa-ai/vexa/issues/244) (`allow-meeting-api` NetworkPolicy missing transcription-gateway egress target — silently breaks batch transcribe under default-deny)
  - [#234](https://github.com/Vexa-ai/vexa/issues/234) (chart hardcodes release name `vexa-platform-vexa-redis` in `redisConfig.url` — breaks any non-default release name, e.g. operators using `helm install vexa-prod` or `helm install vexa-staging` instead of the default `vexa-platform`)
  - [#231](https://github.com/Vexa-ai/vexa/issues/231) (delete stale `values-staging.yaml` + `values-test.yaml`)
- **symptom (user-visible)**: every one of these fires on a fresh `helm upgrade` against a tainted multi-pool K8s cluster. Specific failure shapes:
  - `0.10.4` upgrade leaves redis Pending forever (no toleration) → meeting-api can't start
  - `helm upgrade` with bumped image tag fails with `Job.batch is invalid: spec.template: field is immutable`
  - `capacityReserve.replicas: 0` is silently rendered as `replicas: 3`
  - Fresh install: `mc mb` runs before MinIO is ready → no buckets → recordings fail with `NoSuchBucket`
  - Batch transcribe returns 500 with empty error after ~120 s (NetworkPolicy default-deny silently severs the path)
  - Any deploy with non-default release name floods `redisConfig.url` consumers with `getaddrinfo ENOTFOUND vexa-platform-vexa-redis`
- **severity**: **P0 / production-deploy class.** Every one of these blocks an external operator from running OSS Helm on a multi-pool cluster (any cluster with dedicated bot / GPU / DB / generic pools — i.e. essentially every production K8s cluster). Most operators today work around them via local `.tgz` patches, manual `kubectl delete job` between every upgrade, or operator-side retry loops. Pure chart fixes; no application-code change.
- **scope shape (groom view; plan finalises)**:
  - **A.1 — restore tolerations / nodeSelector / affinity templates** (`deployment-redis.yaml`, `deployment-pgbouncer.yaml`, `job-migrations.yaml`). The 0.10.4 diff dropped these `{{- with .Values.global.tolerations }}` blocks; restore them and add a sub-chart values pattern for `redis.tolerations` / `migrations.tolerations` for finer control. ~3 template diffs.
  - **A.2 — migrations Job becomes a Helm hook** — add `"helm.sh/hook": pre-upgrade,pre-install` + `"helm.sh/hook-delete-policy": before-hook-creation`, `"helm.sh/hook-weight": "0"`. Standard Helm pattern; closes #272#3 + #247 in one diff.
  - **A.3 — minio-init Job race** — replace `sleep 5` with a polling loop on `mc ready vexa` (or equivalent) up to 120 s. Drop `hook-delete-policy: hook-succeeded` so the failed Job stays for debugging; add `hook-failure-policy: keep` analog. Use the official `minio/mc` image's `ready` subcommand if present in the pinned tag.
  - **A.4 — `capacityReserve.replicas: 0` honoured** — replace `{{ .Values.capacityReserve.replicas | default 3 }}` (Go-template falsy-zero trap) with `{{ if hasKey .Values.capacityReserve "replicas" }}{{ .Values.capacityReserve.replicas }}{{ else }}3{{ end }}`. Or just document `enabled: false` as the disable knob — plan picks. Trivial.
  - **A.5 — NetworkPolicy `allow-meeting-api` egress to transcription-gateway** — add transcription-gateway to the egress target list. Optionally introduce a `tx-gateway.enabled` pattern that toggles the rule.
  - **A.6 — release-name hardcoding in service references** — replace `redisConfig.url: "redis://vexa-platform-vexa-redis:6379/0"` with helper-derived `{{ include "vexa.fullname" . }}-vexa-redis`. Audit ALL hardcoded `vexa-platform-*` strings in `values.yaml` + templates; same fix shape. **Cross-reference 260421's Iteration-3 Pack G regex-drift lesson** — verify with `helm template -n test --release-name=vexa-prod` rendering before merge (any non-default release name; `vexa-prod` / `vexa-staging` / `acme-meetings` are all common operator choices).
  - **A.7 — delete stale values files** — remove `values-staging.yaml` + `values-test.yaml` that no operator should be using; `values.yaml` + `values-production.yaml` + `values-lke.yaml` (if it exists) are the canonical set.
- **regression checks (plan picks shape)**:
  - `HELM_UPGRADE_TWICE_NO_JOB_IMMUTABLE` — script-mode: `helm upgrade` an installed release with a different image tag, expect zero `field is immutable` errors. Closes A.2 regression.
  - `HELM_FRESH_INSTALL_BUCKETS_PRESENT` — script-mode: fresh `helm install`, wait for completion, `mc ls vexa/` shows the expected buckets. Closes A.3.
  - `HELM_CAPACITY_RESERVE_ZERO` — script-mode: render with `capacityReserve.replicas: 0` and grep for `replicas: 0`. Closes A.4.
  - `HELM_NETWORK_POLICY_TX_GATEWAY` — grep-mode: `allow-meeting-api` NetworkPolicy has tx-gateway egress rule.
  - `HELM_NO_HARDCODED_RELEASE_NAME` — grep-mode in rendered chart: zero occurrences of `vexa-platform-vexa-` outside `_helpers.tpl`. Closes A.6.
  - `HELM_TOLERATIONS_PROPAGATED` — grep-mode in rendered chart with `global.tolerations` set: every Deployment/StatefulSet/Job has matching `tolerations:` block. Closes A.1.
- **estimated scope**: **~1.5–2 days.** Six template diffs + value-file cleanup + ~6 static-or-script regression checks. The fixes are individually small; the cost is running them through validate end-to-end on a fresh LKE cluster.
- **repro confidence**: HIGH — #272 is a single integrated reporter-verified bundle; #234, #244, #247, #249, #231 each have minimal repro steps in their bodies.
- **owner feature(s)**: `infrastructure` (chart shape — same family as 260421 Packs A/G).

### Pack B — Bot pod scheduling: `runtimeProfiles` tolerations + env propagation  (**recommended: YES, P0**)

- **sources**:
  - [#240](https://github.com/Vexa-ai/vexa/issues/240) (chart `runtimeProfiles` defaults missing `node_selector` + `k8s_overrides.tolerations` → bot pods escape pool isolation; on any cluster with dedicated bot/GPU/DB pools, bots land on the wrong pool and autoscaler grows the wrong pool)
  - [#250](https://github.com/Vexa-ai/vexa/issues/250) (runtime-api profile yaml schema doesn't accept `nodeSelector` / `tolerations` / `affinity` at all — fix needs to land in BOTH places: chart values AND profile schema)
  - [#235](https://github.com/Vexa-ai/vexa/issues/235) (runtime-profiles ConfigMap doesn't propagate `RECORDING_ENABLED`, `STORAGE_BACKEND`, `MINIO_*` / `S3_*`, `MEETING_API_URL` to bot pods → bot transcribes but silently skips recording upload)
  - cross-ref: [#272 issue 2 reporter context](https://github.com/Vexa-ai/vexa/issues/272) — reporter's "bin-pack `vexa.ai/pool` taint model" is the same shape #240 documents; all bot pods need the toleration to land on the bot pool.
- **symptom**: a single class of bug — bot pods are NOT chart-managed Deployments; they're created at runtime by `runtime-api`'s K8s backend reading the `vexa-runtime-profiles` ConfigMap. That ConfigMap omits two critical surfaces: scheduling (#240, #250) and storage env (#235). Result: bot pods schedule on the wrong node pool AND silently skip recording. Both surfaces have to land together — fixing #240 alone (add fields to chart) without #250 (extend profile schema in runtime-api code) means the YAML change is silently ignored.
- **severity**: **P0 / silently-broken feature.** This is the single highest-leverage gap for any operator running multi-pool K8s. The 04-23 staging-isolation rehearsal observed prod pool scaled 5→9 nodes because of stage bot pressure with empty nodeSelector. Recording silently skipping is a separate gold-grade bug — bot transcribes (visible UI), but no S3/MinIO upload (hidden until user opens the dashboard).
- **scope shape**:
  - **B.1 — runtime-api profile schema accepts scheduling fields** — extend `runtime-api/src/profiles.yaml` schema + the `services/runtime-api/runtime_api/backends/kubernetes.py` mapper (already reads `node_selector` at L131 and `tolerations` at L141-144 per #240, but the schema rejects unknown keys). Add `nodeSelector` (camelCase) + `tolerations` + `affinity` to the schema. Mirror the K8s native shape exactly so plain pod-spec snippets work without remapping.
  - **B.2 — chart `runtimeProfiles` defaults populate scheduling fields** — `values.yaml`'s `runtimeProfiles.{meeting,browser-session}` get `nodeSelector` + `tolerations` defaults (driven from `global.botNodeSelector` / `global.botTolerations` at the top level of values, mirroring `global.nodeSelector` / `global.tolerations` for chart-managed Deployments). Operator can override per profile.
  - **B.3 — propagate recording/storage env to bot pods** — `runtimeProfiles.{meeting,browser-session}.env` adds `RECORDING_ENABLED`, `STORAGE_BACKEND`, `MEETING_API_URL`, plus the matching MinIO/S3 secret refs (`MINIO_ENDPOINT`, `MINIO_ACCESS_KEY` via `secretKeyRef`, etc.). Same template pattern that meeting-api's Deployment uses for these envs (see `templates/deployment-meeting-api.yaml` — bot pods inherit the same env block via a helper rather than duplicating it).
  - **B.4 — helper-driven env block** — define `vexa.botStorageEnv` in `_helpers.tpl` (mirrors the secretKeyRef pattern audited in 260421 Iteration 3); bot profile's env block consumes `{{ include "vexa.botStorageEnv" . }}`. Same drift-prevention shape as 260421's Pack A.
- **regression checks**:
  - `RUNTIME_PROFILE_SCHEMA_ACCEPTS_NODESELECTOR` — script-mode unit test against runtime-api: load a profile YAML with `nodeSelector`/`tolerations`, expect no schema error.
  - `BOT_POD_SCHEDULING_FROM_PROFILE` — render-time grep against rendered ConfigMap: profile yaml has non-empty `nodeSelector` + `tolerations` when chart values have `global.botNodeSelector` set.
  - `BOT_POD_RECORDING_ENV_PROPAGATED` — render-time grep against rendered ConfigMap: profile env contains `RECORDING_ENABLED`, `STORAGE_BACKEND`, `MEETING_API_URL`, `MINIO_BUCKET`, etc.
- **estimated scope**: **~1.5–2 days.** B.1 is the only code change (runtime-api schema + tests); B.2/B.3/B.4 are chart edits. Includes one cluster-deploy + dispatch-bot validation that the bot pod actually gets the toleration AND uploads its recording.
- **repro confidence**: HIGH — #240 and #235 each have observed-failure timelines on real staging clusters; #250 has the schema-rejection error.
- **owner feature(s)**: `infrastructure` (chart) + `bot-lifecycle` (runtime-api profile schema). New DoDs: `bot-pods-respect-pool-isolation`, `bot-pods-have-storage-env`.

### Pack C — Redis client robustness layered fix (issue #267 — central platform fix)  (**recommended: YES, P0, single highest-leverage**)

- **sources**:
  - [#267](https://github.com/Vexa-ai/vexa/issues/267) (full layered L1–L4 fix; reproduced incidents 2026-04-21 + 2026-04-26)
  - [#248](https://github.com/Vexa-ai/vexa/issues/248) (runtime-api no Redis retry+backoff on startup → CrashLoopBackOff if Redis not Ready) — same family
  - [#269](https://github.com/Vexa-ai/vexa/issues/269) (dashboard WS lost updates after dispatch — likely silent pubsub hang from #267) — closes when L1 lands
  - `vexa-platform/docs/incidents/2026-04-21-redis-storage-cascade.md` — visible cascade instance of the same root cause
  - `vexa-platform/docs/incidents/2026-04-26-meeting-api-collector-silent-hang.md` — silent-hang instance with **10.5 h data loss + 20 lost transcripts**
  - **Platform-side input (this groom)**: `redis.stop-writes-on-bgsave-error: yes` (the OSS-chart default via Redis's own default) was the trigger of the 04-21 cascade; vexa-platform overrides to `no`. Recommend making `no` the OSS default — folded in as **C.5b** below.
- **symptom**: every `aioredis.from_url(...)` call across the OSS services lacks `socket_timeout`, `socket_connect_timeout`, `socket_keepalive`, `health_check_interval`. Default behavior: redis-py awaits the socket forever; OS TCP keepalive default is 7200 s. Two reproduced failure modes:
  - **Silent hang (04-26)**: `xreadgroup(..., block=2000)` sits inside a single `await` for hours after a Redis pod kill or kube-proxy conntrack flip. No exception, no log line, no traceback. Liveness probe stays green. Consumer is alive in K8s's view; dead in reality. 10.5 h of zero DB writes, 20 meetings' transcripts permanently lost (Redis stream `MAXLEN` aged them out before recovery).
  - **Visible cascade (04-21)**: Redis hits `MISCONF` (storage-mount went read-only, BGSAVE failed, default `stop-writes-on-bgsave-error: yes` tripped), refuses writes for 46 min. Client TCP connections stay open but every write is rejected. After Redis recovers, client pools never reconnect.
- **severity**: **P0 / data-loss class.** This is the single highest-leverage fix on the open backlog. L1 alone (3-line patch × ~5 services) would have prevented BOTH reproduced incidents. L3 (liveness probe based on stream lag) caps the worst-case data-loss window for any future failure mode at ~90 s instead of hours.
- **scope shape (mirrors #267's recommended order)**:
  - **C.1 (L1) — robust client config across all aioredis call sites** — grep `aioredis.from_url\|redis.from_url\|Redis(host=` across `services/{meeting-api,runtime-api,api-gateway,bot-manager,vexa-bot}` and add the 5-keyword block from #267 (`socket_timeout=10`, `socket_connect_timeout=5`, `socket_keepalive=True`, `health_check_interval=30`, `retry_on_timeout=True`). Tests with fakeredis can keep current config — only production clients need this.
  - **C.2 (L3) — `/health/collector` liveness probe with stream-lag check** — meeting-api adds the endpoint from #267's L3 spec (`xinfo_groups` lag > 100 + max consumer idle > 60 s = 503). Wire it as `livenessProbe` on the meeting-api Deployment in the helm chart with `initialDelaySeconds: 60, periodSeconds: 30, failureThreshold: 3`. Worst-case data loss drops from "hours" to "~90 s + restart time" for any future undiscovered failure mode.
  - **C.3 (L2) — task-restart callback for collector tasks** — `_restart_on_crash` done-callback on `consume_redis_stream`, `process_redis_to_postgres`, `consume_speaker_events_stream` per #267's L2 sketch. Belt-and-braces — catches a different failure class than C.1 (in-process exceptions in the consumer body that today silently kill the task).
  - **C.4 (UPGRADED — startup readiness gate, replaces "retry-then-skip")** — argued through-thread on [#267](https://github.com/Vexa-ai/vexa/issues/267#issuecomment-4325493899). The current shape (`try: redis.ping() except: redis_client = None; continue`) is a textbook anti-pattern — kubelet sees `/health` 200, Service routes traffic, consumer is silently dead for the lifetime of the pod. A pure retry-and-eventually-skip is only marginally better: same silent-degraded outcome, just delayed.
    - **C.4.1 — `startup_complete: bool` flag** in meeting-api state. Flips True only after BOTH `redis_client.ping()` succeeds (with bounded retry + exponential backoff inside the ping helper, e.g. 20 attempts × 0.5–10 s) AND `xgroup_create` + `consume_redis_stream` + `consume_speaker_events_stream` + `process_redis_to_postgres` tasks are started.
    - **C.4.2 — `/readyz` returning 503 until `startup_complete`** — wired as `readinessProbe` on the meeting-api Deployment. Existing `/health` stays as livenessProbe (HTTP-loop alive). C.2's `/health/collector` stays as a separate livenessProbe for the consumer-stall case.
    - **C.4.3 — remove the try/except around the startup hook** — let bounded-retry exhaustion propagate. K8s `restartPolicy: Always` + exponential restart backoff handles wait-for-redis ordering deterministically. **No silent-degraded mode anywhere in the codebase.**
    - **C.4.4 (defense-in-depth, optional) — initContainer `wait-for-redis`** on the meeting-api Deployment. Pure declarative ordering; doesn't replace C.4.1–C.4.3 (runtime crashes still need them); makes startup ordering explicit at manifest level.
    - **#248 (runtime-api Redis startup retry)** is a subset of this — runtime-api gets the same `/readyz` gate + bounded ping retry + raise, plus an initContainer if appetite is there. Same pattern, two services.
    - **Invariant guaranteed once C.1–C.4 land**: at no point in the pod's lifetime is there an aioredis call without bounded timeout, a consumer task that can die unnoticed, OR a Ready pod with no working consumer.
  - **C.5 (L4 — Redis maxmemory hygiene, optional)** — explicit `--maxmemory 768mb --maxmemory-policy allkeys-lfu --client-output-buffer-limit "normal 0 0 0"` in `values.yaml` `redis.extraArgs`. Plus a Prometheus alert (`RedisMaxmemoryNear`). **Plan should decide if C.5 lands here or in vexa-platform** — the alert is more naturally on the platform side, but the chart values defaults are OSS-appropriate.
  - **C.5b (PRINCIPLE-PAIRED — AOF + `stop-writes-on-bgsave-error: no` together, never separately)** — Redis durability has two orthogonal mechanisms: AOF (per-write append-only file, the modern default for durability) and BGSAVE (snapshot, useful for fast restart). The 04-21 cascade was triggered by BGSAVE failing while `stop-writes-on-bgsave-error: yes` (default) froze writes for 46 min. Setting `stop-writes-on-bgsave-error: no` ALONE creates a write-loss window if AOF is also off — Redis accepts writes that aren't durable anywhere. **Principled pairing**: enable AOF (`appendonly yes`, `appendfsync everysec`) **AND** set `stop-writes-on-bgsave-error: no`. Both, or neither. AOF is the per-write durability mechanism; BGSAVE failures become non-blocking because AOF still captures writes. Industry-standard Redis-as-stream-buffer config. Add both to `values.yaml` `redis.extraArgs` (or `redis.config`): `--appendonly yes --appendfsync everysec --stop-writes-on-bgsave-error no`. **Closes the 04-21 cascade trigger directly without opening a write-loss window.** Regression check: render-time grep for both flags together; refuse rendering with one without the other (template-time `required` guard).
- **regression checks**:
  - `REDIS_CLIENT_HARDENED_TIMEOUTS` — grep-mode against `services/**/{*.py,*.ts}`: every `aioredis.from_url(...)` constructor includes `socket_timeout` + `health_check_interval`. (Already a TRACKED warning in the platform's `OSS_AIOREDIS_HARDENED_TIMEOUTS` check per #272#5; this lands the OSS-side fix.)
  - `MEETING_API_COLLECTOR_LIVENESS` — script-mode: simulate stream lag (push 200 entries to `transcription_segments`, do not consume), `GET /health/collector` returns 503 within 60 s.
  - `RUNTIME_API_REDIS_STARTUP_RETRY` — chaos-mode regression: start runtime-api with Redis NOT yet Ready, expect zero CrashLoop in the first 60 s; then bring Redis up, expect Ready within 30 s.
  - `MEETING_API_COLLECTOR_RECOVERS_AFTER_REDIS_KILL` — chaos-mode regression (extends existing `r1-redis-pod-kill-recovery.sh` per #267): after killing Redis pod and waiting for it to recover, assert `transcriptions` table writes resume within 10 s. Today this would silently fail.
  - `REDIS_BGSAVE_ERROR_NONBLOCKING` — grep-mode in rendered chart: rendered Redis args include `--stop-writes-on-bgsave-error no`. Closes C.5b regression.
- **estimated scope**: **~1.5–2 days.** C.1 is mechanical (grep-and-edit ~5 services). C.2 is the new endpoint + chart wiring (~1 hr). C.3 is ~15 lines in one file. C.4 is ~10 lines. C.5 is values + alert (the alert lands on the platform side; OSS gets the values). C.5b is one line. The bulk of the time is the chaos-test extension to assert post-recovery PG-write resumption (the existing test only asserts no-5xx during outage).
- **repro confidence**: HIGHEST in this groom — both incidents are documented and reproduced; #267 has the exact line-level fix and test sketch.
- **owner feature(s)**: `infrastructure` (Redis hygiene) + `realtime-transcription` (collector liveness) + new DoD class `redis-client-hygiene`. Likely the cycle's centerpiece.
- **architectural note**: this pack is also the upstream half of vexa-platform's `OSS_AIOREDIS_HARDENED_TIMEOUTS` TRACKED warning (#272#5). When this lands, vexa-platform's downstream watchdog (`cronjob-collector-watchdog` per #267 commit `56fbb35`) becomes redundant. Add to ship-stage follow-ups: "platform repo can retire collector-watchdog CronJob after this cycle merges."

### Pack D — Bot lifecycle on K8s (orphan pods)  (**recommended: YES, HIGH**)

- **sources**:
  - [#261](https://github.com/Vexa-ai/vexa/issues/261) (K8s backend returns `container_id = pod.metadata.uid`; meeting-api stores it; later `DELETE` looks it up by NAME and silently 404s → orphan pod forever)
  - [#266](https://github.com/Vexa-ai/vexa/issues/266) (`DELETE /bots/{platform}/{native_meeting_id}` schedules container-stop as a `BackgroundTask` with no retry/dead-letter/reconciliation; meeting transitions to `COMPLETED` while pod keeps running indefinitely; reproduced today in 20-bot scale test)
  - [#258](https://github.com/Vexa-ai/vexa/issues/258) (browser-session entrypoint's `wait` keeps the container alive forever after `node dist/docker.js` exits; pod stays Running for VNC access but no signal to K8s)
  - cross-ref to [#272 issue 6](https://github.com/Vexa-ai/vexa/issues/272) (bot pod logs ephemeral — debugging this class is what's hard) → covered in Pack G below
  - cross-ref vexa-platform's `gaps.md` "No pod garbage collector" item — this OSS cycle delivers the upstream half (correct DELETE), platform gets to decide if it still needs the GC reconciler
- **symptom**: three independent bugs that each leave bot pods consuming K8s slots indefinitely. Reproduced in production: 3-of-20 DELETEs in a scale test returned 500 with the meeting marked `COMPLETED` but the pod still recording for 12+ minutes (#266). Pattern: at any point of failure (wrong identifier in #261, fire-and-forget in #266, wrong entrypoint in #258), the meeting-api / runtime-api state diverges from the actual K8s pod state, with no reconciliation.
- **severity**: **HIGH.** Cluster capacity slowly bleeds. Recordings continue consuming MinIO bytes for orphan pods. In a multi-tenant cluster this is a noisy-neighbor / cost problem; in a tightly-bin-packed production it's a capacity-exhaustion problem. Each individual bug is small; the trio compounds.
- **scope shape**:
  - **D.1 — fix the `container_id` identifier** (#261) — runtime-api's K8s backend returns pod NAME (`meeting-23-00efafef`) as `container_id`, not pod UID. Same field, different value. Plus a Registry static check (`grep`-mode) that confirms `pod.metadata.name` is what's wired to the response. ~5-line fix.
  - **D.2 — DELETE container-stop becomes durable** (#266) — replace the FastAPI `BackgroundTask` `_delayed_container_stop` with the same `webhook_retry_worker` Redis-Stream pattern that 260421's Pack J used for exit callbacks. Push the stop intent to a stream; a worker (already in meeting-api's process or a sibling task) pops + retries with exponential backoff + DLQ. Idempotent stop on the runtime-api side (stop-already-stopped is a 200 no-op). Same shape that 260421's Pack J already validated for exit callbacks; don't reinvent.
  - **D.3 — browser-session entrypoint signals exit** (#258) — change `wait` (no args) to `wait $NODE_PID` so the entrypoint exits when node exits. VNC services become opt-in via `KEEP_ALIVE_AFTER_NODE_EXIT=true` env. Pair with `terminationGracePeriodSeconds: 30` in the browser-session profile so K8s reaps cleanly. ~5-line shell diff.
- **regression checks**:
  - `K8S_BACKEND_CONTAINER_ID_IS_NAME` — script-mode unit test on runtime-api: dispatch a pod, assert the response's `container_id` equals `pod.metadata.name`.
  - `BOT_DELETE_DURABLE_RETRY` — chaos-mode integration test (mirrors 260421 Pack J): dispatch a bot, blackhole runtime-api, DELETE the bot, restore runtime-api, assert pod is reaped within `IDLE_CHECK_INTERVAL`.
  - `BROWSER_SESSION_POD_TERMINATES_ON_NODE_EXIT` — script-mode integration test: dispatch a browser-session bot, kill node from inside the container, assert pod transitions to `Succeeded` (or `Completed`) within 60 s.
- **estimated scope**: **~1–1.5 days.** D.1 + D.3 are tiny. D.2 is the larger piece — but the pattern is already validated by 260421 Pack J, so it's roughly "instantiate the existing pattern on a new entry surface."
- **repro confidence**: HIGH for all three — each issue has a reproduction with kubectl-level evidence.
- **owner feature(s)**: `bot-lifecycle` (correct lifecycle on K8s). New DoD: `bot-pod-deletion-is-durable`.
- **architectural note**: 260421's Pack J introduced the durable-delivery pattern in runtime-api's `idle_loop`. Pack D.2 here applies the same shape to meeting-api's container-stop path. Architecturally consistent — no new infrastructure.

### Pack E — Recording durability across pod churn  (**recommended: YES, HIGH** — but two-track decision)

- **sources**:
  - [#268](https://github.com/Vexa-ai/vexa/issues/268) (recording finalize JSONB write coupled to S3-upload request lifetime; lost across meeting-api pod restart; reproduced today on prod meeting 11010 — 53 chunks in S3, JSONB empty until manual recovery)
  - [#272 issue 1](https://github.com/Vexa-ai/vexa/issues/272) (Zoom Web/SDK audio loss on short / abrupt-stop meetings — Zoom never had periodic chunk upload; revert of `58ba53e feat: incremental audio recording` in `24e0641`)
- **symptom**: two distinct recording-loss surfaces, both rooted in "recording state is implicit in a single in-flight request":
  - **#268**: meeting-api pod restart during a meeting drops the `is_final=true` JSONB finalization. S3 has the chunks, but `meetings.data.recordings[].media_files` is `[]`. User-visible as empty recording in dashboard.
  - **#272#1**: Zoom Web meetings stopped within 30 s lose all audio. Reorder `performGracefulLeave()` so audio upload happens FIRST; OR re-apply the reverted incremental upload commit (`58ba53e`) for Zoom paths, mirroring what gmeet/teams already have via 260421's Pack B redesign.
- **severity**: **HIGH.** #268 silently loses recordings on every helm upgrade or OOM. #272#1 silently loses recordings on every short Zoom meeting (any meeting < 30 s, plus any meeting where shutdown hits a hang in voice-agent / pipeline cleanup that consumes the budget).
- **scope shape**:
  - **E.1 — recording finalize moves to outbox pattern (#268)** — `POST /internal/recordings/upload` does the S3 upload, then `XADD` a `recording_finalizations` stream entry with the chunk metadata. A background task (same `webhook_retry_worker` pattern that already exists in meeting-api) consumes the stream and ACID-writes `meetings.data.recordings[].media_files`. Idempotent (keyed off `chunk_seq` + `media_type` + `session_uid`). Pending stream entries survive any number of meeting-api restarts.
  - **E.2 — Zoom incremental upload (#272#1, "Asks 1+2")** — re-apply commit `58ba53e` (incremental audio upload every 10 s for Zoom paths), reconciled against current `recordings.py` + `profiles.yaml` + `tests3/Makefile` (the issue notes that the cherry-pick is non-trivial; cleanest is a re-implementation). PLUS reorder Zoom's `performGracefulLeave()` so audio upload runs FIRST, before UI/voice/pipeline/PulseAudio/mux cleanup. Defensive — even if SHUTDOWN_TIMEOUT_MS is consumed by other hangs, the recording is durable.
  - **E.3 (NEW — remove `_delayed_stop_finalizer`, replace with idle_loop sweep)** — argued through-thread on [#268](https://github.com/Vexa-ai/vexa/issues/268#issuecomment-4325495528) in response to platform-side finding of the in-process APScheduler timer. After 260421-prod-stabilize Pack J shipped durable exit-callback in runtime-api's `idle_loop`, the 90 s in-process timer in meeting-api is **redundant defense** — two competing mechanisms for the same `stopping → completed` transition. Production evidence: rows with `status='stopping'` for >24 h after a meeting-api restart in the 90 s window.
    - **E.3.1 — remove `_delayed_stop_finalizer`** (and the APScheduler dependency in meeting-api, if there are no other consumers — audit at plan time).
    - **E.3.2 — add a meeting-api `idle_loop` sweep** mirroring runtime-api's Pack J pattern. Every `IDLE_CHECK_INTERVAL`, scan `status='stopping' AND updated_at < now() - threshold` (recommend `threshold = 5 × IDLE_CHECK_INTERVAL`, default 300 s). Force-complete with `completion_source: stale_stopping_sweep`. Idempotent. **Loud** on logs — every sweep that finds rows is a signal that the durable callback didn't fire when it should have (diagnostic, not silent recovery).
    - **E.3.3 — regression check**: chaos-mode test that (a) dispatches a bot, (b) DELETEs it (status → `stopping`), (c) blackholes runtime-api so the exit callback can't deliver, (d) restarts meeting-api during the would-be timer window, (e) restores runtime-api, (f) asserts the meeting transitions to `completed` within `IDLE_CHECK_INTERVAL`.
  - **E.3 cross-linked also from [#235 comment](https://github.com/Vexa-ai/vexa/issues/235#issuecomment-4325496960)** — the "zombie meeting 10745" secondary symptom is the same root.
- **regression checks**:
  - `RECORDING_FINALIZE_SURVIVES_POD_RESTART` — chaos-mode integration test: dispatch a bot mid-recording, kill meeting-api, restore, send the `is_final=true` chunk, assert `media_files: [...]` populates within 30 s.
  - `ZOOM_RECORDING_SURVIVES_30S_ABRUPT_STOP` — tier-meeting-mode test against a real (or fixture'd) Zoom meeting: join, click stop within 30 s, assert at least one chunk reaches MinIO.
  - `BOT_GRACEFUL_LEAVE_UPLOADS_FIRST` — grep-mode in `services/vexa-bot/core/src/platforms/zoom/{web,native}/`: `performGracefulLeave()` calls audio-upload before UI-leave / voice-agent-cleanup / mux.
- **estimated scope**: **~2–3 days.** E.1 mirrors 260421 Pack B's outbox shape for a different surface (chunk uploads instead of webhooks). E.2 is a re-implementation of the reverted commit with conflict resolution — non-trivial but scoped. Total includes a chaos test (E.1) + a Zoom-side smoke (E.2).
- **repro confidence**: HIGH for E.1 (#268 has prod meeting 11010 evidence + manual-recovery script). HIGH for E.2 (#272 has the exact reverted commit hash + the failure-mode timeline).
- **owner feature(s)**: `realtime-transcription` (recording sub-feature) + `bot-lifecycle` (Zoom shutdown ordering). New DoDs: `recording-finalize-survives-pod-restart`, `zoom-recording-survives-abrupt-stop`.
- **two-track decision**: E.1 and E.2 are independent. If capacity is tight, **E.1 alone** is the higher-impact pick (every operator hits #268 on every helm upgrade; only Zoom users hit #272#1). Or split into E-now (E.1) + E-later (E.2 in a separate Zoom-recording-hardening cycle).

### Pack F — Helm-on-LKE-with-pool-taints smoke matrix  (**recommended: YES, MED, structural**)

- **source**: [#272 cross-cutting ask](https://github.com/Vexa-ai/vexa/issues/272) (last paragraph: *"Per-release smoke matrix says compose / lite / helm all green. The above 4 blockers all surfaced specifically in the Helm-on-LKE-with-pool-taints scenario, which is plausibly the production deployment shape for many users."*)
- **symptom**: every blocker in Pack A (and most of Pack B) was caught by the reporter on their staging cluster, NOT by the OSS release smoke matrix. The matrix exercises `compose / lite / helm-on-fresh-untainted-cluster` — fine for unit-equivalent tests, blind to the production deployment shape. The same gap will produce the next layer of similar bugs three weeks from now.
- **severity**: **MED — structural.** No single user-facing impact; rather, this is the gap that lets all the other Pack A/B bugs reach production. Closing it is what makes future stabilization cycles diminish in volume.
- **scope shape (groom view; plan finalises)**:
  - **F.1 — extend the helm validate matrix** with a "production-shape" mode: `make validate-helm-prod-shape` that:
    - provisions a small LKE cluster (or kind cluster with simulated taints) with a tainted bot pool + an untainted base pool (mirrors #240's reproduction shape)
    - applies `nodeSelector` + `tolerations` to chart values (driven from `global.botNodeSelector` / `global.botTolerations`)
    - runs `helm install` then `helm upgrade` with a bumped image tag (forces Job recreation — catches #247 / #272#3)
    - dispatches a real (or fixture'd) bot, lets it run < 30 s, calls DELETE — catches #272#1 + Pack D.2
    - asserts: zero `Pending` pods, zero `field is immutable` errors, recording chunk(s) in MinIO, JSONB populated, no orphan pods.
  - **F.2 — add a second mode** (cheaper, runs every PR): `helm template` rendered against a "production-shape" values file, statically asserted to NOT contain hardcoded release names, NOT have empty NetworkPolicy targets, etc. Rolls up into the per-feature DoDs from Pack A.
  - **F.3 — gate decision** — once F.1 + F.2 are green, the per-release smoke matrix gains a `helm-prod-shape` row, alongside `compose / lite / helm`. This becomes a release blocker; a red `helm-prod-shape` row halts merge to main.
- **regression checks**: Pack F IS the regression checks. Plan formalizes the matrix entry.
- **estimated scope**: **~2–3 days.** F.1 needs LKE provisioning shape (or kind+simulated taints — cheaper for CI), the dispatch-bot-and-abort-30s primitive, and the assertion harness. F.2 is the lighter-weight version. F.3 is registry+Makefile wiring (~½ day).
- **repro confidence**: HIGH — the reporter explicitly offers to *"contribute the test scaffold once Issue 1 is in flight"* (#272 last paragraph). Coordinate.
- **owner feature(s)**: `infrastructure` (chart) + the test-harness layer in `tests3/`. New DoD: `helm-prod-shape-validates`.
- **dependency note**: Pack F's effectiveness depends on Packs A/B/D/E landing first — the matrix exercise only catches *future* regressions; existing bugs are caught by their own pack-level checks. Schedule F LAST in develop order.

### Pack G — Bot pod log capture: compliance-coupled to meeting retention  (**recommended: YES, MED, principle-revised twice**)

- **source**: [#272 issue 6](https://github.com/Vexa-ai/vexa/issues/272) (bot pod logs are ephemeral; debugging recordings fails).
- **symptom**: when a bot pod dies, its `[Graceful Leave]` shutdown logs go with it. Every recording-loss bug becomes a "we don't know why" post-mortem unless someone was tailing logs during the failure.
- **severity**: **MED.** Diagnostic robustness multiplier for Pack E + Pack D. Without it, every future bug in those packs takes hours-to-days longer to diagnose.
- **PRINCIPLE-REVISED-TWICE scope** — the first revision (route logs through K8s-native log aggregation, not JSONB) was correct on SoC grounds but missed the data-protection dimension that [PLATFORM] surfaced on #272: bot logs contain meeting metadata that has data-retention implications for any operator running vexa with non-public meetings. If logs flow into an external log aggregator with its own retention policy, they outlive the meeting's `data_retention_days` contract. The OSS chart cannot enforce that on every operator's log infrastructure; the OSS data model CAN.
- **Synthesis (final)**: BOTH stdout-JSON discipline (basic logging hygiene) AND JSONB capture (compliance-coupled to meeting retention) AND opt-in long-lived aggregator (operator-overlay choice).
  - **G.1 — bot pods log structured JSON to stdout** (industry-standard discipline; doesn't preclude anything). Audit `services/vexa-bot/core/src/{platforms,services,utils}/*.ts`; every log line is a single-line JSON object with `{ts, level, msg, meeting_id, session_uid, platform, ...fields}`. Existing prefix-tagged logs (`[Graceful Leave]`, `[Recording]`) become structured fields. **Pure logging hygiene** — required for G.2 to be useful (parsable logs).
  - **G.2 — runtime-api lifecycle captures `kubectl logs <bot-pod> --tail=10000 --previous=false` on exit, persists into `meetings.data.bot_logs`** (JSONB array of structured records). Bounded size: **50 KB per meeting** (raised from 10 KB after [PLATFORM] refinement — verbose graceful-leave + recording-finalize + final decision sequence is the diagnostic-critical window; 10 KB was conservative for free-form text but pessimistic for structured logs). Head-N + tail-N + truncation marker on overflow. **Lifetime-coupled to the meeting**: purged with `meetings` row per operator's `data_retention_days`. Default: ON. Chart values `bot.logCapture.enabled: true` + `bot.logCapture.maxBytes: 51200`. Ship-stage follow-up: flag any meeting hitting >75% of the cap (sentinel for bot-loop bugs).
  - **G.3 — `kubectl logs --previous` for crashes**: when a bot pod exits non-zero, also fetch `--previous` to capture pre-restart container logs. Stored same place as G.2.
  - **G.4 — opt-in sidecar / external log-aggregator pattern** (separate from G.2, NOT default). `runtimeProfiles.{meeting,browser-session}.sidecars: []` extension point in chart values, plus a documented `values-loki-example.yaml`. Default OFF. Operators who want Loki / CloudWatch / Datadog wire it themselves; the chart provides the extension point but doesn't ship the integration. **Operators who turn this ON are explicitly opting their log retention OUT of vexa's `data_retention_days` contract** — documented as a deliberate trade-off in `docs/operations/bot-log-aggregation.md`.
  - **G.5 — docs**: `docs/operations/bot-log-aggregation.md` covers (a) default JSONB-coupled retention model, (b) opt-in sidecar trade-off, (c) the data-protection implications of each, (d) example overlays for Loki/CloudWatch/Datadog. Educational + opinionated about the trade-off.
- **regression checks**:
  - `BOT_LOGS_STRUCTURED_JSON` — grep-mode against `services/vexa-bot/core/src/`: zero `console.log("[` embedded-prefix lines; every log line through a structured logger.
  - `RUNTIME_API_CAPTURES_LOGS_PRE_REAP` — script-mode integration test: dispatch a bot, kill it (SIGKILL), assert `meetings.data.bot_logs` populates within 30 s.
  - `BOT_LOGS_PURGED_WITH_MEETING` — script-mode test against the data-retention enforcement: mark meeting for retention purge, run purger, assert `bot_logs` JSONB is purged with the row.
  - `BOT_LOGS_BOUNDED_SIZE` — script-mode: dispatch a noisy bot, assert `bot_logs` JSONB stays under 10 KB; on overflow, head-N + tail-N + truncation marker is the shape.
- **estimated scope**: **~1.5 days.** G.1 audit (~2 h). G.2 + G.3 capture + JSONB write (~½ d). G.4 chart values + sidecar extension (~2 h). G.5 docs (~2 h). Bounded-size + retention-purge tests (~½ d).
- **repro confidence**: HIGH — well-trodden logging patterns; the only new code is the runtime-api log-fetch-on-exit hook (already has a K8s client wrapper).
- **owner feature(s)**: `bot-lifecycle` (logging + retention) + `infrastructure` (chart). New DoDs: `bot-logs-structured-json`, `bot-logs-captured-pre-reap`, `bot-logs-purged-with-meeting`, `bot-logs-bounded-size`.
- **principle alignment**:
  - **G.2 default JSONB**: logs treated as data subject to the meeting's retention contract — compliance-coupled by construction, no operator-side log-infra dependency, no risk of logs outliving the meeting.
  - **G.4 opt-in sidecar**: separation of concerns clean — operators with existing log infra can wire it, but they explicitly opt out of the retention contract (documented).
  - **No silent extraction** to anywhere not under the operator's data-retention control. No workaround.

### Pack H — `post_meeting` 503 handling: dedicated terminal state, not "leave processing"  (**recommended: YES, MED, principle-revised**)

- **source**: platform-side input received during groom. Root cause of `vexa-platform/docs/incidents/2026-04-23-post-meeting-collector-503/`. **Not yet filed as a GH issue** — this groom is the formal scope for the fix.
- **symptom**: in `services/meeting-api/meeting_api/post_meeting.py` (function `aggregate_transcription`):
  ```python
  if response.status_code != 200:
      logger.error(f"Collector returned {response.status_code} for meeting {meeting_id}")
      return  # ← silently fails into 'meeting failed' branch
  ```
  A `503` from the gated transcription-gateway endpoint is treated identically to "transcripts not ready" — meeting transitions to `failed`. Real-world trigger 2026-04-23: tx-gateway pod restart during a `post_meeting` request.
- **severity**: **MED.** User-visible — meetings get marked `failed` with empty transcript when the actual cause is transient infra. Worse, the `failed` status may dispatch a `meeting.failed` webhook that signals "transcription broken" to downstream consumers when the real state is "transient gateway flap."
- **PRINCIPLE-REVISED scope** (rejecting the "leave in `processing` forever" framing — that was a workaround. A meeting that genuinely can't be aggregated needs a terminal state at SOME point; the principled fix is a DEDICATED terminal state distinguishable from user-meaningful failure):
  - **H.1 — distinguish 5xx from 4xx in the HTTP retry layer** (NOT special-cased per call site): meeting-api's internal HTTP client wrapper (used for tx-gateway + any future internal RPC) gets a retry-on-5xx policy with exponential backoff (5 attempts, 2–30 s). 4xx returns immediately to the caller. This is one place, one policy, applies to every internal-RPC site by construction. Industry best practice: retry-on-transient at the transport layer, not in each handler.
  - **H.2 — introduce dedicated `aggregation_failed` terminal state** distinct from `failed`. State machine: success → `completed`; user-meaningful failure (bot couldn't join, audio path empty, etc.) → `failed`; transient-infra exhaustion after retry budget → `aggregation_failed`. Dashboard renders the two distinctly ("transcription temporarily unavailable, retry available" vs "meeting failed"). Plan stage finalizes the state-machine diagram + DB migration (one new enum value).
  - **H.3 — separate webhook event** `meeting.aggregation_failed` with `retry_after` and `failure_reason` fields. Distinct from `meeting.failed`. Downstream consumers can handle the two cases differently — most will want to ignore `aggregation_failed` and wait for a subsequent `meeting.completed` or `meeting.aggregation_failed_permanent` (the latter only after a manual operator action OR an idle_loop sweep aging the row past a "fully give up" threshold of, say, 7 days).
  - **H.4 — idle_loop sweep retries `aggregation_failed` rows** every `IDLE_CHECK_INTERVAL`, with bounded re-attempt budget (say 24 retries × exponentially increasing intervals up to 1 day, total ~7 days), then transitions to `aggregation_failed_permanent`. Same `idle_loop` pattern as Pack E.3.
  - **H.5 — registry check**: dynamic (script-mode) regression that simulates a 503 from tx-gateway, asserts the call retries (H.1), asserts the meeting transitions to `aggregation_failed` not `failed` (H.2), asserts `meeting.aggregation_failed` event fires not `meeting.failed` (H.3).
- **estimated scope**: **~1 day** (was ~0.5 d in the workaround framing). H.1 retry-layer is ~30 lines + replaces inline retry logic. H.2 + H.3 are a small enum addition + state-machine + webhook-event addition (~50 lines). H.4 is a sweep mirror of Pack E.3 (~30 lines). H.5 is the test.
- **repro confidence**: HIGH — incident has the exact trace; state-machine + webhook-event addition is mechanical.
- **owner feature(s)**: `realtime-transcription` (post-meeting aggregation) + `infrastructure` (internal-RPC retry policy). New DoDs: `transient-5xx-retried-at-transport-layer`, `aggregation-failure-distinguished-from-meeting-failure`.
- **architectural note (cross-cycle)**: this is the fourth instance in this groom of the "transient infra failure → permanent state transition" anti-pattern (Pack C: hung Redis socket → silent stall; Pack D: failed DELETE → orphan pod; Pack E: pod restart → lost finalize; Pack H: 503 → meeting failed). Plan should treat the four as one coordinated effort: **system-wide enforcement that "transient ≠ terminal" requires the failing operation to (a) retry deterministically at the transport layer, (b) succeed-or-mark-distinct-terminal-state, (c) be observable**. No silent skip, no "left in processing forever," no two-mechanisms-claiming-same-transition.
- **principle alignment**: industry best practice — fault-isolation through dedicated state classes, retry policy at the transport layer (single source of truth), bounded escalation timeline. No "leave it processing and hope" workaround. `aggregation_failed_permanent` is the legitimate terminal state for "we genuinely could not aggregate after a generous retry budget"; not silent.

### Pack I — Chart availability defaults: replicas + PDB + probes for stateless services  (**recommended: YES, MED, principle-driven**, NEW from principle audit)

- **source**: `vexa-platform/docs/gaps.md` "All services replicaCount=1 with no PDB" + "Missing health probes on subchart services" — both flagged as critical/high. Currently OSS chart ships every stateless service with `replicaCount: 1` and no PDB; admin-api / api-gateway / tts-service / mcp / transcription-collector lack readiness/liveness probes. Industry best practice: any stateless service exposed to clients runs `replicaCount: ≥ 2` with a PDB `minAvailable: 1` and both readiness + liveness probes.
- **symptom**: every operator inheriting OSS-chart defaults gets a degraded-availability deploy. Single-pod services = brief outage on every node drain, every pod crash, every helm upgrade. No probes = K8s can't detect unhealthy services; dead pods keep receiving traffic. This is the same anti-pattern 260421-prod-stabilize Pack G fixed for `apiGateway.replicaCount: 2` + `maxSurge: 0` — extending to the rest of the stateless-service set + adding the PDB + probes layer.
- **severity**: **MED — every operator hits this on day one of running OSS chart in production.** Not as immediately visible as Pack A's helm-upgrade failures (these don't fail loud; they just give bad availability silently), but same operator-pain class.
- **scope shape**:
  - **I.1 — chart values defaults: `replicaCount: 2`** for every stateless OSS service: `apiGateway` (already 2 from Pack G/260421), `meetingApi`, `runtimeApi`, `adminApi`, `dashboard`, `transcriptionGateway`. State-bearing services (Postgres, Redis, MinIO) NOT touched — their replication shape is a bigger architectural call (StatefulSet + replication), defer to a separate cycle. Operators who explicitly want single-replica development deploys override per service.
  - **I.2 — PDB templates for every stateless service**, gated by chart values `<service>.podDisruptionBudget.enabled: true` (default ON in production, OFF in dev). `minAvailable: 1` ensures one pod always serves during voluntary disruption (node drain, helm upgrade with `maxSurge: 0`). Industry-standard pairing with `replicaCount: 2`.
  - **I.3 — readiness + liveness probes for every stateless service template**:
    - `readinessProbe: httpGet /healthz` — refuse traffic until ready (Pack C's `/readyz` for meeting-api is a more specific case; I.3 is the baseline shape)
    - `livenessProbe: httpGet /healthz` — restart pods that hang
    - `initialDelaySeconds`, `periodSeconds`, `timeoutSeconds`, `failureThreshold` set to industry-standard defaults
  - **I.4 — startup probes** for services with cold-start latency (whisper, tts-service if shipped) — separates startup from steady-state thresholds.
  - **I.5 — `terminationGracePeriodSeconds: 30`** explicit on every stateless service — gives in-flight requests time to drain on rollout. (260421's Pack B fix for bots is a longer 90 s version; I.5 is the baseline 30 s.)
- **regression checks**:
  - `CHART_STATELESS_SERVICES_REPLICACOUNT_AT_LEAST_2` — grep-mode in rendered chart with default values: every stateless Deployment renders with `replicas: 2` (or `replicas: ≥ 2`).
  - `CHART_STATELESS_SERVICES_HAVE_PDB` — grep-mode: every stateless Deployment has a sibling `PodDisruptionBudget` resource by default.
  - `CHART_STATELESS_SERVICES_HAVE_PROBES` — grep-mode: every stateless Deployment template includes both `readinessProbe` and `livenessProbe` blocks.
  - `CHART_STATELESS_SERVICES_GRACE_PERIOD` — grep-mode: every stateless Deployment has `terminationGracePeriodSeconds: ≥ 30`.
- **estimated scope**: **~1 day.** Mechanical. ~6 services × 4 template additions each + matching values defaults + PDB templates + grep checks. Includes one cluster-deploy validation that rolling restarts work without downtime under default config.
- **repro confidence**: HIGH — every change is values + template; verifiable by `helm template` rendering.
- **owner feature(s)**: `infrastructure` (chart). New DoD class: `chart-availability-defaults`.
- **principle alignment**: industry best practice for K8s stateless services. No fallbacks (probes don't fall back; they fail-fast and let kubelet handle it). No workarounds (pure declarative chart shape, what every other production K8s chart ships).
- **cross-reference to Pack C**: meeting-api's `/readyz` (Pack C.4.2) is the specialized case of I.3. The two compose cleanly: `/readyz` adds the consumer-task-alive check on top of `/healthz`'s HTTP-loop alive check.

---

## Cross-cutting principle: alerting is part of robustness, not optional

Per [PLATFORM]'s principle audit on #272: every sweep, every retry-budget-exhausted path, every "canonical mechanism failed" recovery in this cycle MUST be paired with an alert. Auto-recover + warning-log alone is itself a workaround — it lets the canonical mechanism keep failing without operators knowing. **The auto-recover is acceptable IFF accompanied by guaranteed observability.**

Alert inventory for 260427:

| sweep / retry-exhausted | alert name | metric | severity |
|---|---|---|---|
| Pack E.1-sibling: `sweep_unfinalized_recordings` finds rows | `MeetingApiSweepUnfinalizedRecordingsFiring` | `meeting_api_sweep_unfinalized_recordings_total` rate > 0 | warning |
| Pack E.3.2: `sweep_stale_stopping` finds rows | `MeetingApiSweepStaleStoppingFiring` | `meeting_api_sweep_stale_stopping_total` rate > 0 | warning |
| Pack H.4: `aggregation_failed` rows aging past retry budget | `MeetingApiAggregationFailedAccumulating` | `meetings_aggregation_failed_total` rate > $threshold | warning |
| Pack H.4: `aggregation_failed_permanent` transition | `MeetingApiAggregationGivenUp` | `meetings_aggregation_failed_permanent_total` rate > 0 | critical |
| Pack C: `/health/collector` 503 (consumer stalled past kubelet failureThreshold) | (kubelet kills + restarts — alert via standard `KubePodCrashLooping` rule) | n/a | n/a |
| Pack D.2: container-stop outbox DLQ entry | `MeetingApiContainerStopDLQ` | `meeting_api_container_stop_dlq_total` > 0 | warning |
| Pack C.5: Redis maxmemory ≥ 85% | `RedisMaxmemoryNear` | `redis_memory_used_bytes / redis_memory_max_bytes > 0.85` | warning |
| **Cross-cutting: `idle_loop` heartbeat** (added per [PLATFORM] refinement) | **`MeetingApiIdleLoopStalled`** | `time() - meeting_api_idle_loop_last_iteration_timestamp > 5 × IDLE_CHECK_INTERVAL` | **critical** |

Lands as `templates/prometheusrule.yaml` in the chart, gated by `monitoring.prometheusRule.enabled: true` (default ON when kube-prometheus-stack CRDs are detected). README note: hitting any of these alerts is an investigation trigger, not noise.

**Watcher-of-the-watcher**: `idle_loop` IS the durable mechanism for E.1-sibling, E.3.2, and H.4 sweeps + `aggregation_failed` retries. A stalled loop = silent regression for every sweep at once. Heartbeat metric (`meeting_api_idle_loop_iterations_total` + `meeting_api_idle_loop_last_iteration_timestamp`) emitted at end of every iteration; `MeetingApiIdleLoopStalled` alert fires when last-iteration timestamp goes stale. Same external-observable shape as kubelet's `KubePodCrashLooping`.

---

## Cross-cutting: cross-repo split with `vexa-platform`

Every pack in this groom is OSS-side. The platform-side counterparts already in `vexa-platform/docs/gaps.md` should explicitly NOT be in scope:

| platform-side gap (NOT this cycle)              | reason                                                      |
|-------------------------------------------------|-------------------------------------------------------------|
| All services replicaCount=1 + PDB disabled      | platform-tier decision, not chart-default territory          |
| Backup CronJobs / restore drill                  | platform infra layer                                        |
| postgres-exporter + Grafana slot-saturation alert | already routed in 260421 Iteration 1 (incident N-2)        |
| ValidatingAdmissionPolicy for empty secret envs  | already routed in 260421 (incident N-3)                    |
| PgBouncer in transaction mode                    | already routed in 260421 (incident N-5; deferred)          |
| Test credentials in git                          | webapp-side, separate repo                                  |
| HPA for platform services                        | platform decision                                           |
| Rate limiting on bot creation                    | platform-side gateway concern                               |
| DNS not in Terraform                             | platform infra                                              |
| `cronjob-recording-reconciler` (#268 workaround) | becomes redundant after Pack E.1 lands; platform retires it |
| `cronjob-collector-watchdog` (#267 workaround)   | becomes redundant after Pack C.1 lands; platform retires it |
| Dashboard auto-DELETE bot (meeting 11054)        | webapp/dashboard surface; out of k8s/Helm/platform theme    |

The middle two rows (`cronjob-recording-reconciler`, `cronjob-collector-watchdog`) are explicit follow-ups to track in this cycle's ship stage: when the OSS fixes merge, vexa-platform retires its downstream watchdogs.

---

## Suggested cycle shapes — human picks

### Shape 1 — Full stabilization  (**my recommendation**)

- Pack A (chart shape bugs from #272 + #234/#247/#249/#244/#231) — P0 chart deployability
- Pack B (bot scheduling + storage env via `runtimeProfiles`) — P0 silent-broken feature
- Pack C (Redis client robustness, #267 layered fix + #248 startup retry + C.5b BGSAVE default) — P0 data-loss class
- Pack D (orphan-pod lifecycle on K8s, #261/#266/#258) — HIGH cluster hygiene
- Pack E (E.1 recording finalize outbox, #268; E.2 Zoom incremental upload, #272#1) — HIGH durability
- Pack F (Helm-prod-shape smoke matrix) — MED structural (catches the next layer)
- Pack G (bot pod logs captured pre-reap, #272#6) — MED cheap
- Pack H (post_meeting 503 handling — NEW) — MED tiny
- **DEFER**: see "Out-of-theme" list below

Total: **~10–12 days develop** + validate + human. Larger than 260421 (~5 d) and 260426 (~3 d). This is the broadest k8s/Helm cycle in the repo's history. Justification: every pack ties back to at least one reporter-verified incident or a documented production-deploy failure, and the failure modes compound (Pack A without Pack B leaves bot pods broken; Pack C without Pack F lets the next Redis-class regression slip through). Treating this as a single cycle keeps the chart in a coherent shape rather than landing partial fixes that break the next operator's deploy.

### Shape 1-lean — Drop Pack F (smoke matrix)  (**fallback**)

- Same as Shape 1 minus Pack F.
- Total: **~7–9 days**. Loses the structural defense; gains one cycle's velocity. The next operator-deploy regression in 4–6 weeks costs us another stabilization cycle. Plan can re-time-budget at any point.

### Shape 2 — Tight: data-loss + chart deploy + post_meeting only  (**fallback if capacity is tight**)

- Pack A + Pack C + Pack E.1 + Pack H. Drop Packs B, D, E.2, F, G.
- Total: **~5.5 days.** Closes the four highest-severity classes (production-deploy, silent-data-loss, recording-finalize-on-restart, post_meeting-failed-on-503). Leaves bot pool isolation broken (#240/#250/#235), orphan pods accumulating (#261/#266/#258), Zoom audio loss on short meetings (#272#1), no smoke matrix improvement, no pod logs.
- This is the smallest shape that closes the actively-firing production fires from the last week. Recommend ONLY if a follow-on cycle is already scheduled for B+D+E.2+F+G.

### Shape 3 — Minimum: #267 + Pack A only

- Pack A + Pack C only. Smallest possible single release.
- Total: **~3.5 days.** Closes the chart-deploy class + the data-loss class. Leaves everything else broken.
- **Not recommended** — Pack B silent-skip-recording is too high-impact to defer past one more cycle, Pack D is cheap (~1 day) and reduces ongoing operator pain, and Pack H is ~0.5 day for a clear win.

### Shape 4 — Recording-only deep-dive

- Pack E (E.1 + E.2) + Pack G only. Spend the cycle on recording-durability across all platforms.
- Total: **~3.5 days.** Defers everything else (including #267, the data-loss-class fix). **Not recommended** — leaves the Redis-class data-loss exposed for another cycle.

---

## Out of theme (NOT recommended for this cycle, ROUTE to next groom)

These are real open issues that the user's filter excludes (k8s/Helm/platform only). Each gets one line; none gets invented into a pack.

| # | title | reason out-of-scope |
|---|-------|--------------------|
| 270 | leave meeting if all other users are bots | bot UX feature |
| 265 | render meeting times in user's local TZ | dashboard hygiene |
| 264 | dashboard /meetings hardcodes limit=50 | dashboard hygiene |
| 263 | GET /bots returns 354KB JSONB blob | API/dashboard perf |
| 262 | epic: meeting video recording validation | epic — separate cycle |
| 256 | epic: segment reconciliation research | epic — separate cycle |
| 255 | epic: bot lifecycle refinement | epic — separate cycle (overlaps Pack D in spirit but broader) |
| 253 | epic: Zoom Meeting SDK recovery | epic — already in flight (260426-zoom is the Web half) |
| 252 | epic: Teams reliability | epic — separate cycle |
| 251 | epic: audio-capture investigation | epic — separate cycle |
| 246 | arch: Split BotConfig | epic — architectural, separate |
| 245 | configurable transcription latency env | feature, not platform |
| 243 | meeting-api transcribe httpx timeout 120 s | gateway timeout, adjacent but different surface |
| 242 | POST /bots dedup race | bot dispatch race, different family |
| 241 | post-meeting transcribe gateway timeout | gateway timeout (related to Pack H but different — H is response-handling, #241/#243 is request-shape) |
| 238 | voice_agent_enabled does not set cameraEnabled | bot config |
| 237 | bot intermittent zero-segment capture | audio path — #251's territory |
| 236 | api-gateway /ws no Redis pubsub subscribe | adjacent to #267 but different surface; consider if Pack C fixes it as side-effect |
| 233 | bot exit incorrectly classified 'failed' | bot lifecycle classification |
| 226 | Teams 'Continue without audio' modal | bot UX |
| 224 | failed recording after 53 min | recording — adjacent to Pack E but different bug |
| 223 | k8s deployed transcription not working | older bug, may need separate triage |
| 222 | admin panel always returns 401 | webapp/auth, hosted only |
| 204 | Google Meet ScriptProcessor stops audio | audio path |
| 198 | make all infinite loop on non-interactive shells | test infrastructure, separate |
| 194 | fuzzy text matching Whisper/captions | research |
| 193 | store caption data in DB | feature |
| 192 | Teams caption-driven speaker detection | research |
| 191 | Teams VAD-based segmentation | research |
| 190 | bot doesn't auto-leave | bot UX |
| 189 | bot leaves on participant join | bot UX |
| 175 | docs `@vexaai/transcript-rendering` | docs |
| 171 | Teams admission_false_positive | bot lifecycle (Teams) |
| 169 | bot timeout field name mismatch | bot config |
| 168 | voice_agent_enabled defaults True | bot config |
| 167 | initVirtualCamera unconditional | bot config |
| 166 | bot exits admission_false_positive | bot lifecycle |
| 159 | configurable data retention | feature, large |
| 158 | per-meeting RBAC for MCP/API | feature |
| 157 | Silero VAD state drift | research |
| 156 | Parakeet-TDT vs Whisper benchmark | research |
| (NEW, unfiled) | dashboard auto-DELETE bot (meeting 11054) | webapp/dashboard, NOT k8s/platform — file separately, defer to webapp-stabilization cycle |

If the human wants to pull any of these IN, I can re-cluster — but the recommendation is to keep this cycle tight on the platform/k8s theme.

---

## Halt

`groom` stops here. `scope.yaml` is `plan`'s output.

### Action items independent of cycle approval (issue hygiene)

These are **not** scope items for this cycle, but they're surfaced by the cross-reference and worth handling alongside groom approval:

1. **Consolidate #272 against existing issues** (process action). Comment on #247 with platform's repro + Makefile workaround, comment on #267 linking #272#5 as a confirming repro, and re-title #272 to its unique content (issues 1, 2, 4, 6 — i.e. the rows in Pack A.1, A.4, E.2, G). Routes to: ship-stage follow-up of THIS cycle, OR done immediately by whichever side has throughput. Either is fine; recommend doing it now while the context is fresh.
2. **File the dashboard auto-DELETE issue** (process action). New "needs investigation" issue with the meeting-11054 evidence (`status_transition` records showing 4 DELETE calls in 5 s from dashboard pod IP without user click) + DevTools repro suggestion. NOT in this groom's scope (webapp/dashboard surface), but should be filed to start tracking.
3. **File the post_meeting 503-handling issue** (process action). Same as #2 but for the bug Pack H fixes — fileable now with the `2026-04-23-post-meeting-collector-503` incident as evidence. Pack H's scope IS the fix; the issue is the GH-side tracker. **Plan can wire Pack H's DoD against the issue number once it exists.**

These three actions are independent of the cycle shape decision. Recommend the human approve the groom AND green-light the platform side to do all three immediately (or queue them as ship-stage follow-ups). Either flow works.

### Waiting on human (cycle shape + slug)

- [ ] Pick cycle shape: **Shape 1 (recommended, ~10–12 d)** / Shape 1-lean (~7–9 d, drops F) / Shape 2 (~5.5 d, A+C+E.1+H) / Shape 3 (~3.5 d, A+C) / Shape 4 (~3.5 d, E only — not recommended).
- [ ] Slug pick: **`k8s-stabilize`** (broadest theme, mirrors `260421-prod-stabilize`) / **`helm-stabilize`** (chart-leaning) / **`k8s-platform`** / other.
- [ ] Pack-by-pack approval (A / B / C / D / E.1 / E.2 / F / G / H) — same shape as 260421's per-pack signoff.
- [ ] Confirm cross-repo split: every "out of OSS scope" platform gap stays in `vexa-platform`. Confirm vexa-platform's `gaps.md` is the source of truth for the platform-side roster (no duplication in OSS DoDs).
- [ ] Decide if Pack C.5 (Redis maxmemory hygiene + alert) lands here (OSS chart values) or only on the platform side (Prometheus alert rule).
- [ ] Decide if Pack E splits into E.1-now + E.2-later (Zoom incremental upload as a separate Zoom-recording-hardening cycle) — or bundles together this cycle.
- [ ] Confirm "out of theme" deferral list is correct — anything to pull in?
- [ ] Green-light the three issue-hygiene actions above (consolidate #272, file dashboard auto-DELETE, file post_meeting 503).

### Follow-ups deferred to plan (not blocking groom→plan)

- **Required reading for plan**:
  - [#272](https://github.com/Vexa-ai/vexa/issues/272) full body (4 blockers + 2 followups)
  - [#267](https://github.com/Vexa-ai/vexa/issues/267) full body (layered L1–L4)
  - [#268](https://github.com/Vexa-ai/vexa/issues/268) full body (outbox pattern)
  - `vexa-platform/docs/incidents/2026-04-26-meeting-api-collector-silent-hang.md` (incident-doc grounding for Pack C)
  - `vexa-platform/docs/incidents/2026-04-23-post-meeting-collector-503/` (incident-doc grounding for Pack H — read `postmortem.md` + `timeline.md`)
  - `tests3/releases/260421-prod-stabilize/groom.md` Iteration 3 (architectural-conformance audit pattern — apply same audit to every pack here at plan time)
- Plan's architectural-conformance audit (per `260421-prod-stabilize/groom.md` Iteration 3 pattern):
  - **Pack A**: confirm the chart's existing tolerations pattern (in `deployment-meeting-api.yaml` etc) is the canonical shape; A.1's restoration extends it rather than introducing.
  - **Pack B**: confirm `runtime-api/src/profiles.yaml` schema's existing fields (`image`, `command`, etc) — verify the new `nodeSelector`/`tolerations` fit cleanly without breaking existing profile-loading.
  - **Pack C**: confirm `webhook_retry_worker` shape from 260421 Pack J is the canonical "durable async worker" idiom; Pack C.3 task-restart callback follows it. Confirm `redis.config` vs `redis.extraArgs` is the canonical surface for the BGSAVE flag (C.5b).
  - **Pack D.2**: same as Pack C — outbox pattern reuse, not reinvention.
  - **Pack E.1**: same shape as D.2 — chunk-finalize stream → consumer worker. One pattern, three users (webhook retry, exit callback, recording finalize, container stop).
  - **Pack G.1**: confirm meeting-api's K8s client wrapper has a logs-fetch primitive or needs to add one.
  - **Pack H**: confirm the existing httpx-call shape in `aggregate_transcription` — it should leverage the same retry-on-5xx machinery as any other internal RPC, not invent a new one. Plan checks if such machinery exists.
- Plan's `registry_changes_approved` must include at minimum: 6 checks for Pack A, 3 for Pack B, 5 for Pack C (incl. C.5b), 3 for Pack D, 3 for Pack E, 1 for Pack F (rolls up the others), 1 for Pack G, 1 for Pack H — total ~23 checks across the cycle. Sounds heavy; most are grep-mode (cheap to author + cheap to run).
- Cross-repo follow-ups (track in ship-stage):
  - vexa-platform retires `cronjob-collector-watchdog` after Pack C.1 lands.
  - vexa-platform retires `cronjob-recording-reconciler` after Pack E.1 lands.
  - vexa-platform's `gaps.md` "No pod garbage collector" item gets marked RESOLVED-by-OSS once Pack D.2 lands (with a note that platform may still want a GC reconciler as belt-and-braces).
  - vexa-platform may relax the `OSS_AIOREDIS_HARDENED_TIMEOUTS` TRACKED warning once Pack C.1 ships.
  - Issue #272 consolidation: re-title to its unique-content rows (Pack A.1, A.4, E.2, G), close-with-cross-link the duplicate parts.

### Advancing (after human approval)

```bash
make release-plan ID=260427-<slug>
```

(The `release-plan` target calls `stage.py enter plan --release 260427-<slug> …` which both advances the stage AND realigns the worktree directory + `.current-stage.release_id`. See `tests3/stages/02-plan.md` step 2.)
