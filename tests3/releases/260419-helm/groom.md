# Groom — 260419-helm

| field        | value                                                              |
|--------------|--------------------------------------------------------------------|
| release_id   | `260419-helm`                                                      |
| stage        | `groom`                                                            |
| entered_at   | `2026-04-19T07:26:19Z`                                             |
| actor        | `AI:groom`                                                         |
| predecessor  | `idle` (prior release `260418-webhooks` was `compose+lite` only)   |
| theme (user) | *"validate helm deployment"*                                       |

---

## Scope, stated plainly

Stand up a fresh LKE cluster, install the helm chart, run the existing validate matrix on `mode=helm`, and see what turns red. No code fixes in scope. This cycle produces **evidence**, not fixes.

If something turns red, triage classifies it and it becomes the scope for the **next** cycle — not this one.

---

## Pack A — Validate helm

- **source**: user (*"we are only interested in validating helm deployment now"*, 2026-04-19)
- **owner feature(s)**: all five that list `helm` in their DoD sidecars — `infrastructure`, `dashboard`, `bot-lifecycle`, `meeting-urls`, `webhooks`.
- **what this cycle does**:
  1. Provision a fresh LKE cluster (`make lke-provision`).
  2. Install the current `dev` branch helm chart (`make lke-setup`) **using load-test-proven parameters from `/home/dima/dev/vexa-platform`** (see below).
  3. Run the full registry matrix on helm (`make validate-helm`).
  4. Aggregate a release report.
  5. Whatever the gate says — green or red — this cycle exits.
- **what this cycle does NOT do**:
  - Does not fix any code. If a DoD is red on helm, this cycle halts at `triage` and the human decides whether the next cycle fixes it.
  - Does not change DoD definitions, registry entries, or feature thresholds.
  - Does not run on `lite` or `compose`.
- **why**: last helm evidence on record is `reports/release-0.10.0-260417-1454.md` (2 releases ago). `260418-webhooks` deliberately skipped helm. Nobody knows if helm still works.
- **prior claim**: at 260417-1454, helm was green overall with `containers` (5/7) failing and `webhooks.spoof` skipped. Everything else passed.
- **estimated scope**: zero code. LKE cost + validate runtime. ~10–30 min wall-clock depending on cluster cold-start.
- **reproducibility confidence**: high — `make lke-helm` is the exact command. Whether it goes green is the question.

### Known-good configuration source — `/home/dima/dev/vexa-platform`

**Architecture (confirmed by the wrapper-chart's `Chart.yaml` + `Chart.lock`):** the proprietary chart declares the OSS `vexa` chart as a subchart dependency and vendors it as a `.tgz`. The wrapper adds proprietary services and supplies load-tested `vexa.*` overrides to tune the OSS subchart.

So reuse has two faces:
1. **`vexa.*` subchart overrides** — tuning numbers (resources, replicas, Redis args, DB pool, security contexts). Values prove themselves in production. **Generic — safe to land in OSS** once stripped of identifiers.
2. **Wrapper-level everything else** — proprietary services, proprietary infrastructure identifiers, proprietary commercial integrations. **Stays proprietary.** Specific names deliberately omitted from this OSS-resident file.

---

### Separation-of-concerns policy (user requirement, 2026-04-19)

**Goal:** OSS helm becomes "working + scalable" by reusing proven tuning. **Constraint:** zero proprietary leak.

**Forbidden in `/home/dima/dev/vexa` (any file, any commit) — categories only; concrete examples live in `vexa-platform`, not in this file, to avoid leaking them into OSS via the very document that forbids them:**

| category                                                    | why forbidden                          |
|-------------------------------------------------------------|----------------------------------------|
| Production domain names (any hosted-environment DNS)        | identifies hosted infrastructure       |
| Managed-DB hostnames                                         | production infrastructure ID           |
| Hosted-mode feature flags + cookie-domain values             | business-model assumption              |
| Cross-chart service references (names of wrapper services)   | points at proprietary wrapper          |
| Billing / auth-provider environment variable names + values  | proprietary commercial integration     |
| Proprietary image repositories                               | proprietary service images             |
| Production-pinned image tags                                 | release-engineering leak               |
| Hosted CORS origins / allow-lists                            | exposes domain list                    |

Reviewers running the `no-leak` check (§plan) must have the concrete disallow-list from `vexa-platform` side — that list stays in the proprietary repo, e.g. `vexa-platform/operations/guards/oss-leak-denylist.txt` (or equivalent). The OSS side carries only the grep *mechanism* and the *categories*, never the tokens themselves.

**Permitted (generic, infrastructure-agnostic, safe to land in OSS):**

| category                          | example                                                                       | reuse path                             |
|-----------------------------------|-------------------------------------------------------------------------------|----------------------------------------|
| Resource requests/limits (numbers)| `apiGateway: cpu 100m/500m, mem 256Mi/512Mi`; `redis: cpu 100m/1000m, mem 256Mi/1Gi` | overlay into OSS `values.yaml` defaults |
| Replica counts                    | `replicaCount: 1` (per-service default)                                       | OSS default                            |
| Security contexts (hardened)      | `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `imagePullPolicy: Always` | OSS `global.*` default           |
| Redis tuning                      | `--maxmemory 1gb --maxmemory-policy allkeys-lru`, persistence 2Gi             | OSS `redis.args` / `redis.persistence` |
| DB connection pool sizing         | `DB_POOL_SIZE=20`, `DB_MAX_OVERFLOW=20`, `DB_POOL_TIMEOUT=10`                 | OSS `meetingApi.extraEnv`              |
| External-DB opt-in pattern        | `postgres.enabled: false` + `credentialsSecretName`                            | already supported in OSS (commit `e99f4f4`) — carry as documented example, no values |
| PDB / NetworkPolicy / PriorityClass **templates** | generic K8s hardening shapes                                   | OSS subchart-level, off by default     |
| ImagePullSecret **support** (not the secret itself) | `global.imagePullSecrets: [ { name: ... } ]` as a configurable | OSS `global` schema                    |

**Enforcement**: the plan stage must generate a `no-leak` check that greps the OSS chart diff for every token in the Forbidden table. If any match — fail plan-approval. Ship this as a permanent guard, not a one-off.

---

### Two possible scopes for this cycle — human picks

Given *"validate helm"* + *"reuse for working, scalable OSS deploy"* are in tension on scope:

**Scope X — Narrow (validate current OSS helm as-is).**
- Just run `make lke-helm` against current `dev` `values-test.yaml`. See what turns red.
- ~Zero code. One LKE spin-up.
- Pro: fast, establishes a clean baseline.
- Con: doesn't improve OSS helm quality this cycle. That's deferred to a follow-up.

**Scope Y — Port-and-validate (reuse generic tuning, then validate).**
- Port the **Permitted** table into OSS `values.yaml` + `values-staging.yaml`. Add the `no-leak` grep check to CI / validate. Then run `make lke-helm`.
- ~1 day of values editing + 1 LKE spin-up + triage.
- Pro: OSS ships with production-proven resource shapes and hardened defaults.
- Con: bigger scope; some settings might fail validate and need tuning back down for `values-test.yaml` (small cluster).

Recommendation: **Scope Y**, because the user's stated intent ("working, scalable helm deployment") isn't satisfied by Scope X alone. But Scope X is a perfectly legitimate de-risked first cycle — plan can split into two releases if the human prefers smaller steps.

---

## Halt

`groom` stops here. `scope.yaml` is `plan`'s output.

User confirmations received (2026-04-19):
- Pack A — **yes**.
- Reuse source — `/home/dima/dev/vexa-platform`.
- Separation-of-concerns — OSS must not carry any of the Forbidden categories listed above.
- **Scope Y picked** (user, 2026-04-19): *"the goal to implement working configuration and validate on helm"*. Cycle does BOTH: port generic/load-tested tuning from proprietary into OSS chart **and** validate the resulting helm deployment. Scope X rejected.

Deferred to `plan` (not blocking groom→plan transition):
- Where the no-leak check lives (CI step / `make` target / pre-commit hook / all) — plan proposes, human signs off.

Advancing now:
```bash
python3 tests3/lib/stage.py enter plan --actor AI:plan
```
