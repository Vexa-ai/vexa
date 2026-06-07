---

# Vexa Release System

> ⚠️ **SUPERSEDED IN PART (2026-06-04): the stage state machine is removed.**
> This engine is now **stateless** — there is no `.current-stage`, no `lib/stage.py`,
> no `stages/*.md`, and no `release-{groom,plan,…,ship,teardown}` Makefile targets.
> Orchestration (*which* step *when*) now lives in the external **pipeline** skills;
> the engine only exposes pure **verb-targets** (`build`/`deploy`/`validate`/`provision`/
> `teardown`/`promote`) documented in [`CONTRACT.md`](./CONTRACT.md). State lives in the
> substrate (pack issue-tags, PRs, branches, image tags), not a marker file.
>
> **Still accurate below:** the registry / regression-corpus (Part 5.x), the three-phase
> validate, deploy/test mechanics, fresh-infra. **Now obsolete below:** §5.5 "Stage state
> machine", the Part 4 migration plan, and any `make release-*` / `.current-stage` flow.
> (A full prose rewrite is pending; git history keeps the original.)

# Part 1 — Why

---

## 1.1 Why: market fast loop

Whoever iterates fastest on market feedback wins.

**Who turns market signal — a bug report, a feature request — into a shipped release, reliably, the fastest.**

Everything downstream serves that clock.

---

## 1.2 Five enablers of fastest iteration

1. **Market fast loop**
  shortest distance from signal (issue, Discord report) to shipped release — this is the one that wins the market
2. **Transparent system**
  features/services READMEs + DoDs + linked implementation, readable by humans **and** AI — both can contribute without ramp-up tax
3. **Cheap regression protection**
  regression drift is the real enemy — fix once, validate against that forever after, at near-zero marginal cost
4. **Reproducibility**
  clean infra every cycle — no "works on my machine", no shared staging that decays under its own history
5. **Stage-awareness — no drift**
  every release cycle is an explicit, discrete state machine. The stages are *listed canonically* in §5.5 — no fuzzy intermediates, no overlaps. Every actor (AI, human, mechanical) **references the current stage at every action**, not just at entry: every skill invocation, every Makefile target, every artifact written, every commit. Without continuous reference, AI infers from file presence, inference drifts, scope creeps between stages, and every other enabler leaks. Enforcement points enumerated in §5.7.

Skip any one and iteration speed collapses.

---

# Part 2 — The model

---

## 2.1 Two halves — State and Flow

```
  STATE                              FLOW
  ─────                              ────

  README                             groom ◄───── issues ◄──── market
    │                                  │                          ▲
    ▼                                  ▼                          │  OUTER loop
  DoD                                plan                         │
    │                                  │                          │
    ▼                                  ▼                          │
  Registry                         develop ◄───── triage          │
     │                                 │             ▲            │
     │                                 ▼             │            │
     │                              provision        │            │
     │                                 │             │  INNER loop│
     │       writes                    ▼             │   (red)    │
     │   ◄────────────              deploy           │            │
     │                                 │             │            │
     │       applied                   ▼             │            │
     └────────────────►             validate ────────┘            │
                                       │                          │
                                       ▼ green                    │
                                     human                        │
                                       │                          │
                                       ▼                          │
                                      ship ──────► teardown ──────┘
```

Validate **reads** the Registry (applies every prior check) *and* **writes** it (adds this release's new bindings) — that bidirectionality is what makes regressions impossible.

**Two feedback loops close the cycle:**

- **INNER** (red-signal iteration) — `validate → triage → develop → deploy → validate` repeats until green. Each iteration is cheap because the mechanical core (§3.4) runs fast; the loop can cycle many times per day.
- **OUTER** (market) — `ship → teardown → market → issues → groom` — external feedback drives the next cycle's scope.

(A third, MIDDLE loop — human finds a gap during §3.7 eyeroll → `human → triage → develop → …` — shares machinery with INNER; §2.3 covers all three by cost.)

---

## 2.2 The flow

```
       groom ─► plan ─► { provision, develop } ─► validate ─► human ─► release
         ▲                                                                │
         │                                                                ▼
         └────────────────── issues ◄─── market ◄────────────────────────┘
```

Scope declared at `plan` (`scope.yaml`).
Every downstream stage consumes it.
Release ships to market. Market sends issues back to groom.

---

## 2.3 Three nested feedback loops

Ordered by cost:

```
  INNER  — system self-validation    (seconds → minutes, ~free)
           develop ◄──► validate
           "does the code compile, run, pass its tests?"
           hardened by the Registry — every prior fix re-runs, so regressions can't recur

  MIDDLE — human validation          (hours, attention)
           validate ─► human ─► plan
           "does it actually work for a human using it?"
           bounded: human does the minimum — UI eyeroll + release-specific checks —
           from a TODO delivered with assets (test URLs, env handed over)

  OUTER  — market                    (days → weeks, real users)
           release ─► market ─► issues ─► groom ─► plan
           market finds bugs AND drives features
           both flow back through the same intake
```

Each loop catches what the cheaper one is blind to.

---

## 2.4 Why nested loops

Defects exist at every layer of the system.

- cheap loop catches compile / test failures
- medium loop catches what automation can't see (browser UI, real Meet, real humans)
- market loop catches what even humans miss in-release (production load, rare configurations, long-tail users)

**You want every defect caught by the cheapest loop that can catch it.**

That's only true if every loop writes its findings back into the state, so next release's cheap loop is smarter than the last one.

---

## 2.5 The system improves itself

```
   market finds a bug
         │
         ▼
   groom creates an issue
         │
         ▼
   plan scopes it with proves[]
         │
         ▼
   develop adds the test + DoD
         │
         ▼
   validate proves it's fixed
         │
         ▼
   state updates — README DoD + Registry entry
         │
         ▼
   next release's inner loop catches it automatically
```

One cycle later: this bug can't recur without the cheap loop firing.

---

## 2.6 Five primitives (from first principles)

Given the five enablers, what *has* to exist? Five things. One per enabler.

**1. Scope** — the per-release contract
   a file that declares "this release is about these issues, and here's how we'll prove each one"
   → delivers enabler #1 (**market fast loop**)

**2. README-as-contract**
   every feature/service has a README; every README lists DoDs; every DoD is owned by a test step
   same file is the ground truth for humans and for AI
   → delivers enabler #2 (**transparent system**)

**3. Registry** — *the accumulated state*
   grows every release (**write**); runs in full every release (**apply**)
   spans automated evidence (`test-registry.yaml`, `checks/registry.json`), human evidence (`human-always.yaml`), and DoD contracts (`features/*/README.md`)
   only grows, never shrinks; removing requires an explicit decision
   *"Gate" = the pass/fail decision (`confidence_min` verdict over applied Registry + reports)*
   → delivers enabler #3 (**cheap regression protection**)

**4. Fresh-infra lifecycle**
   every release: provision from zero → validate → tear down
   → delivers enabler #4 (**reproducibility**)

**5. Stage state machine** — *the cycle's own state*
   explicit, discrete, linear:
   `idle → groom → plan → develop → provision → deploy → validate ⇄ triage → develop → …`
   `validate(green) → human → ship → teardown → idle`
   `.current-stage` is a one-line file; every Makefile target guards on it; illegal transitions hard-fail
   every stage declares: **objective, inputs, outputs, exit condition, and *what it may not do***
   the stage is **referenced at every action** (not just entry) — skills, Makefile targets, artifacts, commits all carry it (see §5.7)
   → delivers enabler #5 (**stage-awareness — no drift**)

Five enablers ↔ five primitives, 1:1.
Everything else — `run-matrix.sh`, `aggregate.py`, skills, Makefile targets, ship logic — is *implementation* of these five.

---

## 2.7 AI vs mechanical — the design rule

**Mechanical** wins on determinism, cost, and speed.
Use it when input is structured, the rule is explicit, and the operation repeats often.

**AI** wins on noise absorption and synthesis.
Use it when input is unstructured and the answer requires interpretation.

**Rule (two parts):**

1. **AI at the ingresses** of the OUTER and MIDDLE loops (where market signal and human signal enter). Mechanical everywhere else.
2. **AI is always stage-aware.** Before any action, AI reads `.current-stage` and asserts what stage it's in. Its objectives, inputs, outputs, and limitations come from the stage definition (§5.5). Drifting outside the current stage is a hard error, not a judgment call.

Why part 1: the **INNER loop**'s value comes from running fast and cheap many times per day — that's what delivers **cheap regression protection**. AI in that path destroys the cost advantage. AI at the ingress earns its keep by converting market signal into **scope** with `proves[]` the INNER loop can chew on.

Why part 2: AI agents pick up work mid-cycle, across sessions, without shared memory of the prior conversation. The ONLY reliable ground truth is the filebase. If they infer stage from file presence, they drift — start coding during plan, run triage during provision, skip human gate, etc. An explicit stage marker makes the constraint visible: *"I am in validate; I may not edit code."* That's what delivers **stage-awareness — no drift**.

---

## 2.8 Loop × actor matrix

Who does what in each loop:


| loop   | mechanical                                                                                                   | AI                                                                                         | human                                                                            | market                            |
| ------ | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- | --------------------------------- |
| INNER  | `run-matrix`, `aggregate`, Gate thresholds, Registry writes, **Fresh-infra lifecycle**                       | classify failure: regression (→ develop) or **gap** (→ fix root cause, no retry-masking)   | —                                                                                | —                                 |
| MIDDLE | TODO render (union of `scope` + `human-always.yaml`); sign-off ledger; `human-always.yaml` appends per scope | prose report → formal issue + proposed `proves[]`                                          | UI eyeroll + release-specific checks, signs off                                  | —                                 |
| OUTER  | GitHub / Discord fetch, issue + PR bookkeeping, teardown                                                     | cluster signal, draft hypothesis, propose `scope` + `proves[]`, draft README / DoD updates | reviews groom, approves scope, **owns README / DoD definitions**, ship / no-ship | delivers bugs AND drives features |


Reads by column:

- **mechanical** is the bulk — the whole INNER loop and the bookkeeping of MIDDLE + OUTER
- **AI** lives only at loop ingresses — the seams where noise → schema
- **human** is absent from INNER **by design** — that's why it's cheap and repeats cheaply; bounded work only in MIDDLE (eyeroll) and OUTER (DoD / scope ownership, ship call)
- **market** participates only at the OUTER ingress — everything downstream is already its converted form

**No "flake" category.** An unreliable check is a **gap** with a root cause — a race, a timing assumption, a deployment fragility, a misowned DoD. Classifying it as "flake" and retrying destroys the INNER loop's trustworthiness, which collapses the whole cost advantage. Robust solutions, not workarounds.

**Current system is backwards:** AI routes stage transitions (INNER interior — should be mechanical); market intake is paste-URL-into-chat (OUTER ingress — should be AI-assisted).

Flipping the line protects the **market fast loop** *and* lets AI do what only AI can do.

---



# Part 3 — The machinery

---

## 3.1 Scope — the per-release contract

Every release has a **`scope.yaml`** — the contract for *this* cycle. Declared once at `plan`; read by every downstream phase.

```yaml
# tests3/releases/260417-webhooks-dbpool/scope.yaml
release_id: 260417-webhooks-dbpool
branch: dev
summary: |
  Webhook delivery hardening + DB pool fix + collector NOGROUP recovery.

deployments:
  modes: [lite, compose, helm]

issues:
  - id: webhook-gateway-injection
    source: human
    problem:    "User-configured webhooks not delivered — user.data has config,
                 meeting.data never gets it."
    hypothesis: "Gateway never injects X-User-Webhook-* headers;
                 admin-api validate_token never returns webhook fields."
    fix_commits: [fd2526c, b953d44]

    proves:
      - {check: webhooks.config, modes: [compose]}
      - {check: webhooks.inject, modes: [compose, helm]}
      - {check: webhooks.spoof,  modes: [compose, helm]}

    required_modes: [compose]
    human_verify:
      - {mode: compose, do: "PUT /user/webhook ...; POST /bots",
                         expect: "response.data.webhook_url == user's URL"}
```

Per issue, scope declares three things:

- **hypothesis** — what we think is broken and how we're fixing it
- **`proves[]`** — bindings into the Registry: *"this issue is fixed iff these checks pass on these modes"*. Same evidence syntax as DoDs.
- **`human_verify[]`** — release-specific manual checks, merged with `human-always.yaml` in the MIDDLE loop.

**Scope vs DoD — two contracts, two scopes:**

| artifact                      | granularity      | lifetime                | role                                                    |
| ----------------------------- | ---------------- | ----------------------- | ------------------------------------------------------- |
| `scope.yaml`                  | per release      | transient (one per cycle, archived) | what *this* release is shipping, bound to Registry checks |
| `features/<name>/dods.yaml`   | per feature      | permanent (evolves with feature)    | what "done" means for the feature, forever             |

Scope's `proves[]` usually **reuses** existing DoD evidence — fixing a bug in webhooks binds to the existing `webhooks.*` checks. When a release adds a new feature, scope declares new DoDs that go into that feature's new `dods.yaml` (added as part of the same release).

**On green release, scope `proves[]` joins the Registry.** That's the ratchet — next release inherits every prior claim.

### Plan gate — human validates objectives and DoDs

The `plan` stage cannot exit without **explicit human approval** of two things:

1. **Objectives** — each issue's `hypothesis` and `proves[]` bindings. Human is asked: *"Do you agree this hypothesis captures the bug, and these Registry checks prove it's fixed?"*
2. **DoDs affected** — any DoD this release will add, remove, or change (weight, evidence, label). Human is asked: *"Do these DoDs accurately describe what 'done' means for the touched features?"*

AI prepares the proposal; human approves line-by-line. Approval is recorded mechanically:

```yaml
# tests3/releases/<id>/plan-approval.yaml
release_id: 260417-webhooks-dbpool
approved_at: 2026-04-18T12:34:56Z
approver: dmitry@vexa.ai

scope_approved:
  - issue: webhook-gateway-injection
    hypothesis: true
    proves: true
    required_modes: true
  - issue: db-pool-fix
    hypothesis: true
    proves: true
    required_modes: true

dod_changes_approved:
  - feature: webhooks
    change: add
    id: reliability-db-pool
    approved: true
  - feature: webhooks
    change: modify-weight
    id: events-meeting-completed
    from: 10
    to: 15
    approved: true

registry_changes_approved:
  - id: webhooks.new-delivery-tracking
    type: script
    approved: true
```

**Plan exit criteria (mechanical, checked by `stage.enter(develop)`):**

- `scope.yaml` exists and parses; every issue has non-empty `hypothesis` and at least one `proves[]` binding
- Every `{check: X}` in `scope.proves[]` either already exists in `registry.yaml` or appears in `plan-approval.yaml` under `registry_changes_approved`
- `plan-approval.yaml` exists and has `approved: true` on every item listed

Any of these fails → `plan → develop` transition hard-fails with the specific missing approval. Plan can't be "mostly approved"; every proposed change has an explicit human yes/no.

**Why this matters:** scope + DoDs are the contract the rest of the cycle runs against. Getting them wrong propagates into every subsequent stage. The plan gate is the cheap place to catch mistakes — a 10-minute review saves hours of wrong-direction development.

---

## 3.2 DoD — the per-feature contract (sidecar)

**Today's mess (what we're replacing):** three different DoD shapes coexist across feature READMEs —

1. `tests3.dods:` YAML frontmatter with `evidence:` bindings (webhooks) — wired to the Gate
2. `tests3.targets` + `tests3.checks` frontmatter (many legacy features) — NOT wired
3. Rich hand-written DoD tables in the README body (gmeet, zoom) — invisible to any parser

`aggregate.py` silently skips anything not in shape #1. A feature can look like it has gold-standard DoD tracking and contribute zero to the Gate.

**Fix: one shape, one place. Sidecar DoD file per feature.**

```
features/webhooks/
├── README.md       prose: why, what, expected behavior, architecture
└── dods.yaml       contract: what "done" means, machine-readable
```

README references the DoD file with one line near the top — no heavy frontmatter:

```markdown
**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 95%**
```

`dods.yaml` is pure contract — hand-written, never machine-mutated:

```yaml
# features/webhooks/dods.yaml
gate:
  confidence_min: 95
dods:
  - id: headers-hmac
    label: "X-Webhook-Signature = HMAC-SHA256(...) when secret is set"
    weight: 10
    evidence: {check: webhooks.hmac, modes: [compose]}
  - id: flow-gateway-inject
    label: "Gateway injects validated webhook config"
    weight: 15
    evidence: {check: webhooks.inject, modes: [compose]}
  # ... more DoDs
```

Each DoD declares four things:

- **id / label** — the claim
- **weight** — contribution to the feature's confidence
- **evidence** — the binding into the **Registry** (`{check: <id>, modes: […]}`)
- **gate.confidence_min** — the per-feature pass threshold (one per file, feature-level)

**One sidecar file per feature is the single source of truth for its DoDs.** The README stays human-readable; the contract stays machine-readable. One evidence syntax, one place to edit.

**No silent skip.** A feature without `dods.yaml` is a hard fail; the only opt-out is `dods: []  # intentionally un-gated, reason: X`. There is no third state.

---

## 3.3 Tracing one DoD — the three files

Follow DoD `headers-hmac` from the webhooks feature through the whole chain.

**1. `features/webhooks/dods.yaml` — the contract**

```yaml
- id: headers-hmac
  label: "X-Webhook-Signature = HMAC-SHA256(...) when secret is set"
  weight: 10
  evidence: {check: webhooks.hmac, modes: [compose]}
```

Reads as: *this claim is verified by running `webhooks.hmac` on mode `compose`, worth 10 points toward the webhooks feature's confidence.*

**2. `tests3/registry.yaml` — the Registry entry**

```yaml
webhooks.hmac:
  type: script                      # grep | http | env | script
  script: tests/webhooks.sh
  step: hmac
  modes: [lite, compose, helm]
  state: stateful                   # stateful | stateless
  mutates: [meetings, bots, webhook_config]
  max_duration_sec: 180
```

Reads as: *to run `webhooks.hmac`, execute `tests/webhooks.sh`; look at the step named `hmac` in its JSON output. It's stateful, touches meetings/bots/webhook_config, and takes up to 3 min.*

Two new fields matter for execution planning (covered below):

- **`state:`** — stateful or stateless. Drives parallel-vs-serial scheduling.
- **`mutates:`** — which shared state the test touches. Used to detect conflicts if two stateful tests collide.

**3. `.state/reports/compose/webhooks.json` — the result**

```json
{
  "test": "webhooks",
  "mode": "compose",
  "image_tag": "0.10.0-260417-1454",
  "started_at": "2026-04-17T21:06:24Z",
  "duration_ms": 178241,
  "status": "pass",
  "steps": [
    { "id": "hmac",   "status": "pass", "message": "HMAC-SHA256 64-char digest" },
    { "id": "inject", "status": "pass", "message": "gateway injected webhook_url=..." }
  ]
}
```

Deterministic JSON, one per test per mode, emitted by the runner via `test_begin / step_* / test_end` helpers in `tests3/lib/common.sh`. Contains timing, image tag, and per-step status — every atom of evidence `evaluate_dod` needs.

---

## 3.4 Validate has three phases

"Validate" isn't one step — it's three sequential phases, each with a different job:

```
     PLAN                EXECUTE               RESOLVE
     ────                ───────               ───────
Decide WHAT to        Run the plan.         Read the results,
run and in            Each script           score the DoDs,
WHAT order.           emits a JSON          compute the Gate
                      report.               verdict.
```

**Why the split matters:**

- **PLAN** — where `state:` / `mutates:` / `duration:` on each Registry entry earn their keep. Stateless checks parallelize for free; stateful serialize to avoid interference. No AI needed; the metadata declares enough. Runs in `lib/run` (today: `run-matrix.sh`).
- **EXECUTE** — pure I/O. Scripts in `tests/`, `checks/run`. Each emits `.state/reports/<mode>/<test>.json` via `test_begin` / `step_*` / `test_end` helpers.
- **RESOLVE** — pure read + arithmetic. DoD evidence → report lookup → DoD status → feature confidence → Gate verdict. Runs in `lib/aggregate.py`. **Detailed next.**

All three phases are mechanical. Together they're the INNER loop's **mechanical core** — AI touches nothing from plan-build to Gate-verdict. That's what makes the core cheap enough to repeat many times per day.

**When the Gate goes red, the INNER loop doesn't end** — it opens the *interpretation seam*: classify regression vs gap (AI `triage` skill), own the next fix (human), loop back to execute. The mechanical core runs fast; judgment concentrates only where the signal is red.

---

## 3.5 Resolve — how a DoD becomes a status

```
DoD headers-hmac:
   evidence:  check=webhooks.hmac, modes=[compose], weight=10
     │
     │   look up webhooks.hmac in registry.yaml
     ▼
   Registry says:  run tests/webhooks.sh, check step "hmac"
     │
     │   for each required mode (here: compose):
     │     open .state/reports/<mode>/webhooks.json
     │     read steps.hmac.status
     ▼
   compose → pass

   All required modes passed → DoD status = PASS → contributes +10 to pass-weight
```

If any required mode is missing or fails, DoD status = `missing` or `fail` → doesn't contribute → confidence drops.

**Per-feature confidence math** (webhooks, 10 DoDs, total weight 100):

```
confidence = Σ(weight of passing DoDs) / Σ(weight of all DoDs) × 100
           = 100 / 100 × 100
           = 100%

Gate check:  confidence ≥ gate.confidence_min (95)  →  feature passes
```

**Write-back, every release:**

- `features/webhooks/README.md` — AUTO-DOD block shows per-DoD status + timestamps
- `tests3/reports/release-<tag>.md` — aggregate summary: confidence + verdict per feature

**Release Gate:** every feature's confidence ≥ its threshold → green. Any single feature fails → block.

**No silent skips.** A feature without `dods.yaml` hard-fails at `load_features`; the only opt-out is an explicit `dods: []  # intentionally un-gated, reason: X`. No third state.

---

## 3.6 AI vs mechanical — overlaid on the release cycle

Who operates at each step of one release:

```
┌─ OUTER (market signal → scope) ─────────────────────────────┐
│                                                              │
│   groom              AI       cluster GitHub + Discord      │
│                               → issue packs + hypothesis    │
│     │                                                        │
│   plan (author)      AI       draft scope.yaml + proves[]   │
│                      Human    review / approve intent       │
│     │                                                        │
│   edit dods.yaml     AI       propose DoD additions         │
│   (when scope adds   Human    own weights + meaning         │
│    new features)                                             │
└──────────────────────────────────────────────────────────────┘
     │
     ▼
┌───── INNER LOOP ─────────────────────────────────────────────┐
│                                                              │
│  ╔═════ mechanical core ═══════════════════════════════╗    │
│  ║ PHASE 1 — PLAN      filter scope × modes,           ║    │
│  ║                     group by state, order            ║    │
│  ║ PHASE 2 — EXECUTE   run per plan, emit JSON reports ║    │
│  ║ PHASE 3 — RESOLVE   evaluate_dod,                    ║    │
│  ║                     compute_confidence,              ║    │
│  ║                     Gate verdict                     ║    │
│  ║ write-back          AUTO-DOD + release.md            ║    │
│  ╚═════════════════════════════════════════════════════╝    │
│     │                                                        │
│     ├─ red  ──► triage + fix   AI     classify: regression  │
│     │                                  vs gap                │
│     │                          Human   own the fix           │
│     │                                  → back to execute     │
│     │                                    (same INNER cycle)  │
│     │                                                        │
│     └─ green ──► exit INNER loop                             │
└──────────────────────────────────────────────────────────────┘
     │
     ▼
         ┌─ MIDDLE (bounded human validation) ─────────────────┐
         │                                                      │
         │   generate TODO    mechanical   human-always +      │
         │                                 scope new_checks +  │
         │                                 URLs/env/assets     │
         │                                                      │
         │   eyeroll          Human        minimum: UI +       │
         │                                 release-specific     │
         │                                                      │
         │   findings         AI (human skill)                 │
         │                    → translate prose to formal issue│
         │                    Human sign off                    │
         └─────────────────────────────────────────────────────┘
             │
             ▼ both gates green
         ship                 mechanical   merge dev→main,
                                           promote :dev → :latest
```

**Rule applied in practice:** AI sits at **ingresses** (OUTER from market, MIDDLE from human) and at the **INNER interpret seam** (triage on red). The *mechanical core* of the INNER loop (plan / execute / resolve / write-back) stays free of AI — that's what keeps it cheap enough to repeat many times per day. Interpretation happens *inside* the INNER loop but only when the Gate goes red.

---

## 3.7 Human — bounded sign-off after Gate green

Automated Gate green is necessary, not sufficient. What automation can't see (UI, real Meet, real humans) still matters. The human stage has **two parts** — both required before ship.

### Part A — Code review

AI-written code must be read by a human before it ships. AI prepares a **structured review packet** so the human can skim and approve quickly:

```
releases/<id>/code-review.md      (auto-generated by AI for human review)
────────────────────────────────
## Per-commit summary
  fd2526c  — gateway.forward_request: inject X-User-Webhook-*
             rationale: webhook config lost between validate_token and meeting.data
             risk: header stripping order matters (strip client-supplied FIRST)
             touched: services/api-gateway/main.py
             DoDs it satisfies: flow-gateway-inject, security-spoof-protection

  b953d44  — admin-api.validate_token: return webhook fields
             rationale: gateway needs webhook_url/secret/events from admin-api
             risk: field shape change — versioned response
             touched: services/admin-api/app/main.py
             DoDs it satisfies: flow-user-config

## Diffs (grouped by concern, not by commit)
  [webhook injection]   (files, +N -M, unified diff)
  [header security]     (...)
  [admin-api response]  (...)

## Risk notes
  - Header stripping order is load-bearing — if validated fields are stripped
    AFTER injection, they'd be lost. Test `security-spoof-protection` covers
    this specifically.
  - admin-api response now includes webhook_url; any caller that logs full
    response will see it. Not sensitive (user-configured), but noted.

## Open questions for the human
  - [ ] Is "strip client headers first, then inject" the right order? (yes/no)
  - [ ] Should webhook_url be masked in admin-api response body? (not today, could be)
```

**Structure of the review packet:**

- **Per-commit summary** — not just what, but *why* (rationale) and *what could go wrong* (risk)
- **Diffs grouped by concern** — readable by intent, not by git order. AI summarizes the diff into 3-5 conceptual groups.
- **Risk notes** — flags invariants that must hold, ordering dependencies, anything a reviewer might miss scanning fast
- **Open questions** — items AI wasn't sure about; human answers these explicitly

Human approves the packet → Part B unlocks.

### Part B — Bounded manual eyeroll

What automation can't see. The human's job is **bounded** — a TODO arrives with assets (test URLs, env) ready to use. No hunting, no rediscovery.

```
Human TODO  =  accumulated human-always.yaml items   (never shrinks)
            +  this release's new human checks       (scope-specific)
            +  eyeroll of features this scope touched
```

Human signs off → MIDDLE-loop Gate green.

Findings during eyeroll:

- Applies every release forever → graduates to `human-always.yaml` (Registry grows on the human side)
- Specific to this release → block ship; file as an issue for next cycle's scope

### Ship gate

**Both** automated Gate **and** human sign-off (Parts A *and* B) → ship. Missing either → block. The sign-off artifact lists all three:

```yaml
# releases/<id>/human-approval.yaml
release_id: 260417-webhooks-dbpool
code_review_approved: true   # Part A
eyeroll_approved: true        # Part B
approver: dmitry@vexa.ai
signed_at: 2026-04-18T14:10:00Z
```

---

# Part 4 — Current state and the plan

---

## 4.1 Gap summary — reality vs the model

Where today's filebase diverges from Part 3. Each gap is downstream of one or more enablers breaking.

**Registry (primitive #3) is split and lossy:**
- Two files share the word *registry* (`test-registry.yaml` + `checks/registry.json`) with different schemas and different parsers. Plus an orthogonal `docs/registry.json` that isn't part of the Gate at all. Three "registries" collectively confuse the vocabulary.
- `checks/registry.json` is hand-written JSON with four tier-schemas coexisting in one file and divider entries (`{"_": "═══ STATIC ═══"}`) as hacks for missing sections. No schema validation.
- Two DoD evidence syntaxes (`{check: X}` vs `{test, step}`) that resolve to the same report.
- No `state:` / `mutates:` on Registry entries → PLAN phase can't order execution intelligently.

**DoDs have three schemas across features:**
- `tests3.dods:` frontmatter — wired to the Gate (only this one)
- `tests3.targets` + `tests3.checks` frontmatter — legacy; parsed by `resolve.py`, not by `aggregate.py`
- Rich hand-written DoD tables in README bodies (gmeet, zoom) — richest format, invisible to every parser
- Any feature without schema #1 is **silently skipped** by `aggregate.py` ("Phase C silent skip"). A feature can look gold-standard and contribute nothing to the Gate.

**Pipeline is triple-encoded:**
- Each stage lives in `SKILL.md` + `Makefile` target + `lib/*.py`. Three sources of truth per stage → drift.
- No `.current-stage` file. The orchestrator infers stage from file presence.
- `run-matrix.sh` mixes bash with embedded Python.

**Execution ignores state:**
- No declared `state: stateful|stateless` per test. All tests run serially today, even the cheap stateless ones. No conflict detection.

**Intake leaks:**
- `0-groom` Discord fetch script lives outside the repo (`/home/dima/dev/0_old/...`). Market signal breaks silently.
- Bug-report translation is manual — human pastes URL, no structured extraction.
- helm mode silently skips scope filter (`Makefile:180`).

**Gate is correct but under-wired:**
- No audit trail on verdict (who approved what).
- No documented rollback path if ship fails between merge and image-promotion.

Every gap below in §4.2 / §4.3 closes one of these.

---

## 4.2 Complete `tests3/` inventory — action per file

Every file under `tests3/` (and a few adjacent), grouped by subsystem. Action: **KEEP** / **CHANGE** / **RENAME** / **DELETE** / **ADD**.

### Registry / Gate (primitives #2 + #3)

| file | action | note |
|---|---|---|
| `test-registry.yaml` | **DELETE** | every entry migrated into `registry.yaml` as `type: script` |
| `checks/registry.json` | **DELETE** | all 73 atomic assertions migrated into `registry.yaml` by type |
| `checks/run` | **CHANGE** | rewrite ~1400 LOC → ~300 LOC thin dispatcher on `type:` field |
| `human-always.yaml` | **KEEP** | MIDDLE-loop accumulated human checks; separate store from INNER Registry |
| `lib/aggregate.py` | **CHANGE** | read `features/*/dods.yaml`; hard-fail on missing DoDs; one evidence syntax (`{check: X}`) |
| `lib/run-matrix.sh` | **CHANGE** | rewrite in pure Python; read `registry.yaml` directly; drop bash+embedded-python split |
| `resolve.py` | **CHANGE** (or DELETE) | currently maps changed files → make targets by parsing legacy `tests3.targets/checks`; after sidecar migration, either rewire to read `dods.yaml` or delete if unused |
| — | **ADD** | `tests3/registry.yaml` — consolidated Registry (one file, `type:` discriminator) |

### Fresh-infra lifecycle (primitive #4) — keep as-is

| file | action | note |
|---|---|---|
| `lib/vm.sh` | **KEEP** | Linode VM base helper |
| `lib/vm-setup-lite.sh` | **KEEP** | provision lite VM |
| `lib/vm-setup-compose.sh` | **KEEP** | provision compose VM |
| `lib/vm-run.sh` | **KEEP** | run workload on VM |
| `lib/vm-reset.sh` | **KEEP** | reset VM to clean state |
| `lib/lke.sh` | **KEEP** | LKE cluster base helper |
| `lib/lke-setup-helm.sh` | **KEEP** | helm install onto LKE |
| `lib/lke-load-db.sh` | **KEEP** | seed DB after provision |
| `lib/detect.sh` | **KEEP** | auto-detect deployment mode |
| `lib/reset/reset-lite.sh` | **KEEP** | reset lite to fresh state |
| `lib/reset/reset-compose.sh` | **KEEP** | reset compose |
| `lib/reset/reset-helm.sh` | **KEEP** | reset helm |
| `lib/reset/redeploy-lite.sh` | **KEEP** | redeploy `:dev` onto lite |
| `lib/reset/redeploy-compose.sh` | **KEEP** | redeploy `:dev` onto compose |

### Middle-loop (human validation)

| file | action | note |
|---|---|---|
| `lib/human-checklist.py` | **KEEP** | generates human TODO from `human-always.yaml` + scope `new_checks` + assets |

### Test scripts (files stay; their test-registry entry moves into `registry.yaml`)

| file | action | note |
|---|---|---|
| `tests/webhooks.sh` | **KEEP** | becomes `registry.yaml` entry, `type: script` |
| `tests/meeting.sh` | **KEEP** | same |
| `tests/meeting-tts.sh` | **KEEP** | same |
| `tests/meeting-tts-teams.sh` | **KEEP** | same |
| `tests/auth-meeting.sh` | **KEEP** | currently `awaiting_retrofit: true` — un-retrofit or mark explicitly un-gated |
| `tests/browser-session.sh` | **KEEP** | same — `awaiting_retrofit` status |
| `tests/admit.sh` | **KEEP** | — |
| `tests/bot.sh` | **KEEP** | — |
| `tests/bot-stop-timing.sh` | **KEEP** | — |
| `tests/browser-login.sh` | **KEEP** | — |
| `tests/containers.sh` | **KEEP** | — |
| `tests/collect.sh` | **KEEP** | — |
| `tests/dashboard-auth.sh` | **KEEP** | — |
| `tests/dashboard-proxy.sh` | **KEEP** | — |
| `tests/finalize.sh` | **KEEP** | — |
| `tests/post-meeting.sh` | **KEEP** | — |
| `tests/transcribe.sh` | **KEEP** | — |
| `tests/transcription-replay.sh` | **KEEP** | replay harness |
| `tests/tts-reliability.sh` | **KEEP** | TTS probe |
| `tests/score.sh` | **KEEP** | scoring harness |
| `lib/common.sh` | **KEEP** | shared shell helpers (`test_begin`, `step_pass`, etc.) |
| `lib/score.py` | **KEEP** | transcription scoring library |
| `lib/replay-score.py` | **KEEP** | replay scoring (transcription-specific) |

### Docs subsystem (orthogonal to Gate)

| file | action | note |
|---|---|---|
| `docs/registry.json` | **RENAME** | → `docs/manifest.json` — not a Registry store; name misleads |
| `docs/check.py` | **CHANGE** | update filename constant after rename |

### Pipeline / orchestration

| file | action | note |
|---|---|---|
| `Makefile` | **CHANGE** | reads `registry.yaml`; drops `smoke-<tier>` wrappers; adds `.current-stage` read/write; absorbs targets for the 7 deleted skills |
| `lib/release-issue-add.py` | **CHANGE** | validate `new_checks` IDs against `registry.yaml` on add (prevent dangling refs) |
| — | **ADD** | `tests3/.current-stage` — explicit pipeline state (replaces LLM inference) |

### Documentation / meta

| file | action | note |
|---|---|---|
| `README.md` | **CHANGE** | update to describe Registry model + sidecar DoDs + single evidence syntax |
| `release-validation.md` | **DELETE** | subsumed by Parts 1–3 of this file (which becomes the README). No separate protocol doc needed. |
| `VALIDATION.md` | **DELETE** | stub redirect to `release-validation.md` — also dead once that's gone. |
| `release-system-review.md` | **KEEP** | this file |

### Data / fixtures — keep as-is

| file | action | note |
|---|---|---|
| `meeting_saved_closed_caption.txt` | **KEEP** | caption sample fixture |
| `testdata/conversations/` | **KEEP** | TTS conversation fixtures |
| `testdata/gmeet-compose-260405/` | **KEEP** | recorded GMeet transcription fixture |
| `testdata/teams-compose-260405/` | **KEEP** | recorded Teams fixture |
| `testdata/test-speech-en.wav` | **KEEP** | audio fixture |

### Release artifacts

| file | action | note |
|---|---|---|
| `releases/_template/` | **KEEP** | scaffolding for new release cycles |
| `releases/260417-webhooks-dbpool/` | **KEEP** | current in-flight release |
| `reports/release-*.md` | **KEEP** | historical aggregate reports (append-only) |
| `.state/reports/<mode>/*.json` | **KEEP** | runtime reports — the clean seam; format unchanged |

### Adjacent (outside `tests3/` but in-scope)

| file | action | note |
|---|---|---|
| `features/*/README.md` | **CHANGE** | strip frontmatter DoDs + legacy `tests3.targets/checks`; delete hand-written body DoD tables; add one reference line to `dods.yaml` |
| — | **ADD** | `features/<name>/dods.yaml` — sidecar DoD contract (the single source of truth) |
| `.claude/skills/1-plan/SKILL.md` | **KEEP** | AI judgment at OUTER→INNER seam |
| `.claude/skills/7-human/SKILL.md` | **KEEP** | AI at MIDDLE ingress (translate human reports) |
| `.claude/skills/0-groom/SKILL.md` | **KEEP** + move Discord script into repo | AI at OUTER ingress (intake) |
| — | **ADD** | `.claude/skills/triage/SKILL.md` — INNER exit: classify failure as regression vs gap |
| `.claude/skills/2-provision/SKILL.md` | **DELETE** | mechanical — Makefile target |
| `.claude/skills/3-develop/SKILL.md` | **DELETE** | not a skill — humans write code; rules belong in lints |
| `.claude/skills/4-deploy/SKILL.md` | **DELETE** | mechanical — Makefile target |
| `.claude/skills/5-iterate/SKILL.md` | **DELETE** | mechanical — Makefile target |
| `.claude/skills/6-full/SKILL.md` | **DELETE** | mechanical — Makefile target |
| `.claude/skills/8-ship/SKILL.md` | **DELETE** | mechanical — Makefile target |
| `.claude/skills/9-teardown/SKILL.md` | **DELETE** | mechanical — Makefile target |
| `.claude/skills/release/SKILL.md` | **DELETE** | router → replaced by `.current-stage` + Makefile dispatch |

---

## 4.3 Migration order (so nothing breaks mid-flight)

1. **Pin the DoD / Registry / validate mental model** in this file (Parts 1–3). Zero code changes. Unblocks every PR that follows. After this step, `release-validation.md` and `VALIDATION.md` can be deleted.
2. **Add** `features/<name>/dods.yaml` sidecars — one PR per feature, migrate frontmatter and body tables in. Old parsers still work; new file is parsed in parallel for diff.
3. **Collapse Registry** — `checks/registry.json` + `test-registry.yaml` → `tests3/registry.yaml` (with per-type split if desired). One-time mechanical migration script.
4. **Rewrite** `aggregate.py` to read sidecars and `registry.yaml`; hard-fail on missing DoDs. One release cycle of parallel-run to validate outputs match.
5. **Delete** old files once parallel-run is green for two releases.
6. **Collapse pipeline** — delete 7 mechanical SKILL.md files; add `.current-stage`; Makefile becomes the pipeline.
7. **Clean up** — rename `docs/registry.json` → `manifest.json`; delete `resolve.py`; regex-rewrite DoD evidence form.

Each step independently revertable. No step depends on the next being done. Order only matters for avoiding merge conflicts on feature READMEs.

---

# Part 5 — Target `tests3/` layout and commands

After the migration, the filebase reads like this. Every path has one role; every role has one primitive or phase behind it.

---

## 5.1 Directory layout (after rewire)

```
tests3/
├── registry.yaml                Registry (primitive #3) — all checks, one schema,
│                                 `type:` discriminator (grep | http | env | script)
│                                 OR `registry/<type>.yaml` if split per type
│
├── human-always.yaml            MIDDLE-loop accumulated human checks (separate store)
├── .current-stage               explicit pipeline state (Part 4 rewire)
│
├── README.md                    this file (Parts 1–3 = the canonical protocol;
│                                 Part 5 = layout + commands reference)
│
├── Makefile                     release-* targets + test invocations (one pipeline entry point)
│
├── lib/                         machinery
│   ├── run                      PLAN + EXECUTE — builds execution graph from
│   │                             registry.yaml × scope.yaml × modes; runs per plan
│   ├── aggregate.py             RESOLVE — load dods.yaml + reports; evaluate_dod;
│   │                             compute_confidence; write AUTO-DOD + release.md
│   ├── common.sh                shared shell (test_begin / step_* / test_end)
│   ├── human-checklist.py       MIDDLE TODO generator (scope + human-always → checklist)
│   ├── release-issue-add.py     scope editing; validates check IDs against registry.yaml
│   ├── vm.sh · vm-setup-*.sh    Fresh-infra (primitive #4) — Linode VMs
│   ├── lke.sh · lke-*.sh        Fresh-infra — LKE cluster
│   ├── detect.sh                auto-detect deployment mode
│   ├── score.py · replay-score.py  transcription scoring (used by test scripts)
│   └── reset/                   mode-specific reset + redeploy scripts
│
├── tests/                       e2e scripts — each is a Registry entry (type: script)
│   ├── webhooks.sh
│   ├── meeting.sh
│   ├── meeting-tts.sh
│   ├── transcribe.sh
│   └── ...  (see Part 4.2 for full list)
│
├── releases/
│   ├── _template/               scaffolding for a new release cycle
│   └── <release-id>/            per-release artifacts (transient)
│       ├── scope.yaml           per-release contract (primitive #1)
│       └── human-checklist.md   MIDDLE checklist, generated per release
│
├── reports/
│   └── release-<tag>.md         aggregate per-release summary (append-only history)
│
├── testdata/                    fixtures (recorded audio, ground-truth conversations)
│
├── docs/
│   ├── manifest.json            (renamed from registry.json) — docs-page catalog
│   └── check.py                 docs-completeness checker (orthogonal to the Gate)
│
└── .state/                      runtime state (gitignored)
    ├── reports/<mode>/*.json    raw evidence from EXECUTE phase
    ├── image_tag · deploy_mode · helm_release   infra state markers
    └── tests3.log               runner log
```

**Feature READMEs (outside `tests3/`):**

```
features/<name>/
├── README.md          prose + one-line reference to dods.yaml
└── dods.yaml          per-feature DoD contract (primitive #2 — README-as-contract)
```

---

## 5.2 Commands — full release cycle

```bash
# ── Stage-by-stage (each an AI skill OR a mechanical Makefile target) ──

make release-groom       # AI skill — cluster GitHub + Discord into issue packs
make release-plan        # AI skill — draft scope.yaml + proves[] bindings
make release-provision   # mechanical — spin up Linode VMs + LKE cluster (primitive #4)
make release-deploy      # mechanical — build :dev image, push to DockerHub,
                         #              pull on every deployment
make release-validate    # mechanical — three-phase validate (PLAN + EXECUTE + RESOLVE)
                         #              on red → `make release-triage`
make release-triage      # AI skill — classify failure: regression vs gap
make release-human       # MIDDLE — generate TODO; wait for human sign-off
make release-ship        # mechanical — merge dev→main, promote :dev → :latest
make release-teardown    # mechanical — destroy Linode + LKE, reset state

# ── Or run the full cycle end-to-end ──────────────────────────────────

make release-cycle       # runs groom → plan → ... → teardown in order,
                         # pausing at AI seams (plan approval, triage, human sign-off)
```

---

## 5.3 Commands — local / inner-loop work

```bash
# ── Validate only the current release's scope ─────────────────────────
make validate SCOPE=<release-id>
make validate SCOPE=<release-id> MODE=compose    # one mode only

# ── Run a single test ──────────────────────────────────────────────────
make run-test TEST=webhooks MODE=compose
./tests/webhooks.sh                              # directly, for local dev

# ── Registry inspection ───────────────────────────────────────────────
make registry-lint                               # schema-validate registry.yaml
make registry-orphans                            # entries with no DoD referencing them
make registry-who-uses CHECK=webhooks.hmac       # reverse: which DoDs bind to this check

# ── DoD inspection ────────────────────────────────────────────────────
make dods-lint                                   # schema-validate all features/*/dods.yaml
make dods-coverage                               # features with no dods.yaml → hard error
                                                  # (except explicit `dods: []  # reason: X`)

# ── Reports + history ─────────────────────────────────────────────────
make report                                      # re-render AUTO-DOD blocks + release-<tag>.md
                                                  # from current .state/reports/
make release-history                             # list all tests3/reports/release-*.md
```

---

## 5.4 Where each primitive lives

| primitive                | contract file                              | runtime data                                   | code that reads/writes                         |
| ------------------------ | ------------------------------------------ | ---------------------------------------------- | ---------------------------------------------- |
| **#1 Scope**             | `tests3/releases/<id>/scope.yaml`          | —                                              | `lib/run` (phase 1), `lib/aggregate.py` (growth) |
| **#2 README-as-contract**| `features/<name>/dods.yaml`                | `features/<name>/README.md` AUTO-DOD block     | `lib/aggregate.py` (read dods, write AUTO-DOD) |
| **#3 Registry**          | `tests3/registry.yaml`                     | `.state/reports/<mode>/*.json`                 | `lib/run` (phase 1+2), `lib/aggregate.py` (phase 3) |
| **#4 Fresh-infra**       | `tests3/releases/<id>/scope.yaml` (modes)  | `.state/deploy_mode` · `.state/image_tag`      | `lib/vm.sh` · `lib/lke.sh` · `lib/reset/*`     |

Four primitives, four files (or file groups), four read/write paths. Every piece of the filebase maps to exactly one primitive or to machinery that serves them.

(The fifth primitive — **Stage state machine** — lives in `.current-stage` + `.state/stage-log.ndjson` + `lib/stage.py`. Detailed in §5.5 and §5.6.)

---

## 5.5 Stage state machine

The pipeline is a **strict state machine**. Stages are **discrete** (no fuzzy intermediates, no overlaps), **specific** (each has a single non-overlapping purpose), and **referenced at every action** (see §5.7). AI reads the current stage before any action; Makefile targets guard on it.

```
idle ─► groom ─► plan ─► develop ─► provision ─► deploy ─► validate
                            ▲                                 │
                            │                                 ├─ green ─► human ─► ship ─► teardown ─► idle
                            │                                 │                │
                            └─────────────── triage ◄─────────┘                │
                                              (red)                            │
                            ▲                                                  │
                            └──────── gap found during human ─────────────────┘
```

| # | stage      | enter from              | objective                                          | inputs                                          | outputs                                    | exit when                         | may NOT do                                  |
|---|------------|-------------------------|----------------------------------------------------|-------------------------------------------------|--------------------------------------------|-----------------------------------|---------------------------------------------|
| 0 | idle       | teardown / *            | dormant between cycles                             | none                                            | —                                          | new cycle → groom                 | any release work                            |
| 1 | groom      | idle                    | cluster market signal → issue packs                | GitHub + Discord                                | draft issue packs                          | human picks packs → plan          | write `scope.yaml`, edit code, touch infra  |
| 2 | plan       | groom                   | produce `scope.yaml` + DoD/Registry change proposals; get human sign-off on both | issue packs | `releases/<id>/scope.yaml` + `releases/<id>/plan-approval.yaml` (human-signed on objectives AND DoDs) | `plan-approval.yaml` complete with `approved: true` on every item → develop | edit code, run tests, touch infra, auto-advance without approval |
| 3 | develop    | plan **or** triage      | write code + tests + `dods.yaml` entries           | `scope.yaml` + (if from triage) triage log      | commits on `dev`                           | all scope commits done → provision (from plan) or → deploy (from triage) | touch infra, run validate   |
| 4 | provision  | develop (first time)    | stand up fresh infra per `scope.modes`             | `scope.yaml`                                    | Linode VMs + LKE up (per scope)            | all deployments ready → deploy    | run tests, edit code                        |
| 5 | deploy     | provision **or** develop | build + push `:dev`, pull on all infra             | current `dev` HEAD + provisioned infra          | deployments running current `image_tag`    | all green → validate              | edit code, run tests                        |
| 6 | validate   | deploy                  | three-phase validate (§3.4)                        | `dods.yaml` + `scope.yaml` + `registry.yaml`    | reports + AUTO-DOD + Gate verdict          | green → human; red → triage       | edit code, change infra                     |
| 7 | triage     | validate (red) / human (gap) | classify regression vs gap; identify next fix | validate failure reports / human report         | triage log; next-fix target                | decision made → develop           | edit code, run tests, run ship              |
| 8 | human      | validate (green)        | (A) code review: human reads AI-generated `code-review.md`. (B) bounded eyeroll: human TODO (scope + `human-always.yaml`) | AI-generated `code-review.md` + human TODO | `releases/<id>/human-approval.yaml` with BOTH parts `true` | both parts signed → ship; gap in either → triage | edit code, change infra, skip code review, auto-sign |
| 9 | ship       | human (signed)          | merge dev→main; promote `:dev` → `:latest`         | both gates green                                | updated main, updated `:latest`            | complete → teardown               | edit code, skip audit entry                 |
| 10| teardown   | ship                    | destroy provisioned infra                          | infra state + release_id                        | clean `.state/`                            | complete → idle                   | run against a `release_id` mismatch         |

**Two load-bearing properties:**

- **"may NOT do" is as enforced as "objective".** Every stage has explicit forbidden actions. AI asked to "fix the bug" while stage is `validate` refuses with its current-stage + may-not-do: *"In validate; may not edit code. Transition to triage → develop to implement the fix."*
- **Code editing is confined to `develop`**, and nowhere else. If code editing needs to happen, the cycle must pass through `develop` — there is no escape hatch.

---

## 5.6 Stage tracking — implementation

**Two artifacts track the stage:**

`tests3/.current-stage` — single-file current-state marker (one line, atomically rewritten on transition):

```yaml
release_id: 260417-webhooks-dbpool
stage: validate            # idle|groom|plan|provision|deploy|validate|triage|human|ship|teardown
entered_at: 2026-04-18T12:34:56Z
last_action: make release-validate
```

`tests3/.state/stage-log.ndjson` — append-only audit log across all releases:

```
{"t":"...","release":"260417-webhooks-dbpool","from":null,"to":"groom","actor":"AI:groom"}
{"t":"...","release":"260417-webhooks-dbpool","from":"groom","to":"plan","actor":"AI:plan+human"}
{"t":"...","release":"260417-webhooks-dbpool","from":"validate","to":"triage","actor":"AI:triage","reason":"red"}
```

Answers *"where are we now?"* (`.current-stage`) and *"how did we get here?"* (log).

**One script enforces transitions — `tests3/lib/stage.py`:**

```python
stage.current()             # read .current-stage → dict
stage.assert_is(expected)   # raises if not in expected stage; use in Makefile / skills
stage.enter(name, actor)    # validate transition is legal (per §5.5), write .current-stage + append log
stage.complete(name)        # record stage completion in log (doesn't advance)
stage.objectives(name)      # return {objective, inputs, outputs, exit, may_not_do} for a stage
```

**Makefile targets guard on stage:**

```makefile
release-deploy:
	@python3 $(T3)/lib/stage.py assert-is provision    # must be in provision
	@# ... do the deploy work ...
	@python3 $(T3)/lib/stage.py enter deploy            # transition on success
```

**AI skills read stage before acting:**

Every AI skill (`groom`, `plan`, `triage`, `human`, and any future skill) opens its action by running:

```python
s = stage.current()
if s["stage"] != EXPECTED_STAGE:
    raise StageError(f"skill '{name}' expects stage '{EXPECTED_STAGE}', got '{s['stage']}'")
obj = stage.objectives(s["stage"])
# obj.may_not_do is the explicit constraint list for the skill
```

That's the drift-prevention mechanism: the skill can't proceed outside its stage, and inside its stage the `may_not_do` list tells it what's off-limits.

**What this gives us:**
- A one-line file read answers *"what's next?"* — no LLM inference from file presence
- Any attempt at an out-of-stage action hard-fails with a clear message
- Every stage transition is logged; the history is replayable for diagnosis
- AI agents picking up work mid-cycle orient themselves immediately — read `.current-stage`, read the stage's objectives, start

**Migration** (added to §4.3 step 6):
- Add `lib/stage.py` + schema for `.current-stage` + initial log
- Add stage guards to every `release-<stage>` Makefile target
- Update every AI skill to read `stage.current()` and assert at entry

---

## 5.7 Stage-reference checklist

"Tracked" is not enough. The stage must be **referenced at every surface where drift could leak**. Five enforcement surfaces — miss any one and drift becomes possible:

**1. AI skill entry — and mid-action**

Every skill's first action is `stage.assert_is(expected)`. Its operating context is loaded from `stage.objectives(current)` — including the `may_not_do` list, which becomes part of its prompt. *Long-running skills re-read `.current-stage` between steps* — not just at entry. Mid-session stage changes (rare but possible) don't leak through.

**2. Makefile target — entry guard AND exit transition**

```makefile
release-<X>:
	@python3 $(T3)/lib/stage.py assert-is <prev>    # entry: hard-fail if wrong stage
	@# ... do the work ...
	@python3 $(T3)/lib/stage.py enter <X>            # exit: transition, append log
```

No target runs unless the previous stage was the legal predecessor (per §5.5).

**3. Artifact metadata — every write stamps the stage**

Every artifact carries the stage it was produced in. Readers can always audit "what stage wrote this?":

| artifact                                | stamp                                                   |
|----------------------------------------|---------------------------------------------------------|
| `releases/<id>/scope.yaml`             | frontmatter: `authored_in_stage: plan`                  |
| `features/<name>/dods.yaml`            | frontmatter: `last_edited_in_stage: develop`            |
| `tests3/registry.yaml` entries         | `last_edited_in_stage:` on each entry                   |
| `.state/reports/<mode>/<test>.json`    | field `emitted_in_stage: validate`                      |
| `releases/<id>/human-checklist.md`     | header: `stage: human`                                  |
| git commit body (during `develop`)     | `release: <id> · stage: develop` trailer                |

Stamping is done by `stage.stamp(data)` — one helper used by every write path.

**4. Human-visible probe — `make stage`**

At any moment, any actor (human or AI) runs `make stage` and sees:

```
release:   260417-webhooks-dbpool
stage:     validate
entered:   2026-04-18T12:34:56Z  (8m 12s ago)
objective: run three-phase validate; produce Gate verdict
inputs:    dods.yaml + scope.yaml + registry.yaml
outputs:   reports + AUTO-DOD + Gate verdict
may NOT:   edit code, change infra, skip human
next:      human (on green) | triage (on red)
```

No inference. One command is the canonical answer to *"where am I, and what's allowed?"*

**5. Precommit / CI guard — hard-fail on cross-stage edits**

Pre-commit hook reads `.current-stage`; commits outside `develop` stage fail unless explicitly overridden. Likewise, a CI check verifies every merged commit references a valid stage in its trailer. Low-cost belt-and-suspenders.

---

**Together these five surfaces make drift impossible without a visible, auditable protocol violation.** Every surface where an actor could leak out of its stage is checked. The stage is *referenced*, not just *recorded*.

| surface              | how drift is caught                                       |
|----------------------|-----------------------------------------------------------|
| AI skill entry       | `stage.assert_is()` at entry; may-not-do in prompt        |
| Makefile target      | entry guard blocks wrong-stage invocation                 |
| Artifact metadata    | readers can audit; mismatched stamps flag drift           |
| `make stage` probe   | actors orient themselves at any moment                    |
| Precommit / CI       | commits outside `develop` refused                         |

---

## 5.8 Stage prompts — AI's canonical operating context

Enforcement (§5.7) tells AI *when* it's in a stage. Prompts tell it *what to do*. Without canonical per-stage prompts, each AI session re-interprets the stage — which is drift by another name.

**Every AI-driven stage has a canonical prompt file:**

```
tests3/stages/
├── 00-idle.md          no-op; stage is dormant
├── 01-groom.md         AI: cluster market signal
├── 02-plan.md          AI + human: scope + approval
├── 03-develop.md       human writes code; AI assists
├── 04-provision.md     mechanical — no AI prompt
├── 05-deploy.md        mechanical — no AI prompt
├── 06-validate.md      mechanical — no AI prompt
├── 07-triage.md        AI: classify regression vs gap
├── 08-human.md         AI generates code-review; human signs
├── 09-ship.md          mechanical — no AI prompt
└── 10-teardown.md      mechanical — no AI prompt
```

Mechanical stages have stage files too (as specs for their Makefile target), but no "AI operating context" section.

**Template every stage file follows:**

```markdown
# Stage: <name>

Actor       AI | human | mechanical | AI+human
Objective   <one sentence>
Inputs      <files, artifacts, deployments expected>
Outputs     <files written, artifacts produced>

## Steps                (AI executes each in order; each is concrete)
1. <action with exact file path or command>
2. <...>

## Exit                 <machine-checkable condition>

## May NOT              <forbidden actions; if asked, HALT and refuse>
- <explicit item>

## Next                 <stage on success> | <on alternate path>

## AI operating context (loaded verbatim into the AI's prompt when this stage is active)
You are operating in stage <name>. Your objective is <one sentence>.
- Read <input files> before any action.
- Produce <output files> by following Steps.
- Exit when <exit condition>.
- You may NOT: <list>. If asked, refuse with: "I am in <name>; I may not X. This requires stage <Y>."
```

**Concrete example — `tests3/stages/07-triage.md`:**

```markdown
# Stage: triage

Actor       AI + human (AI classifies; human decides)
Objective   Given a red Gate, classify every failing DoD as regression or gap,
            and surface the next-fix target for human decision.
Inputs
- `.state/reports/<mode>/*.json`        validate phase output
- `tests3/reports/release-<tag>.md`     aggregate
- `features/<name>/dods.yaml`           failing DoD definitions
- `tests3/registry.yaml`                check definitions + state/mutates
Outputs
- `releases/<id>/triage-log.md`         classification + next-fix target

## Steps
1. `lib/stage.py assert-is triage` → halt if wrong stage.
2. Parse release report; list every DoD with status ≠ pass.
3. For each failing DoD, classify:
   - **regression** → cite the bound check + expected vs actual step output
   - **gap** → cite which property is unreliable (state, timing, infra, test logic).
     Do NOT call it a "flake" — gaps have root causes.
4. Write `triage-log.md` with one entry per failing DoD.
5. HALT. Present to the human. Human designates next-fix target.

## Exit
`releases/<id>/triage-log.md` exists AND contains a human-written line:
`fix this first: <DoD-id>` OR `accept this gap, do not fix`.

## May NOT
- Edit any code (code editing is stage `develop`)
- Run tests, rebuild images, re-provision
- Classify a failure as "flake" without root-cause analysis
- Advance stage without human confirmation

## Next
develop — on human designates next-fix target (usual path)
human — if all failures are accepted gaps (rare)

## AI operating context
You are operating in stage `triage`. Your objective is to classify every failing DoD
as regression or gap, and surface the next-fix target for human decision.
- Read release report + reports/*.json + dods.yaml + registry.yaml before any action.
- Produce `releases/<id>/triage-log.md` — one entry per failing DoD.
- Exit when the log exists and a human has designated the next-fix target.
- You may NOT edit code, run tests, or advance to another stage.
- If asked to implement a fix, refuse: "I am in triage; I may not edit code. That requires stage develop."
- If a failure looks non-deterministic, investigate root cause. Do not classify as "flake".
```

**Why this eliminates interpretation:**

- **Verbatim loading.** Two AI sessions at the same stage read the same file → same behavior. No session-local re-interpretation.
- **Machine-checkable exit.** The `## Exit` line names a specific file + condition. AI doesn't guess when it's done.
- **Explicit refusals.** The `## May NOT` list gives AI a ready refusal script. Out-of-stage asks get a standard response, not ad-hoc judgment.
- **Handoff-friendly.** Session A halts mid-stage; session B picks up by reading `.current-stage` → stage file → resume exactly.

**Layering with existing skills:**

| layer | file | role |
|---|---|---|
| Skill | `.claude/skills/<skill>/SKILL.md` | *agent kind* — "you are a triager" — stable across stages |
| Stage | `tests3/stages/<N>-<name>.md`     | *current constraints* — what CAN / CANNOT be done right now |

The AI's operating prompt at any moment = **Skill prompt + Stage prompt overlaid**. Skill is permanent; stage is the live constraint.

**`stage.objectives(name)` reads from the stage file** — so `make stage` output, AI operating context, and the skill's refusal logic all come from ONE source. Update the stage behavior → edit ONE file → every consumer picks it up.

**Migration note:** add `tests3/stages/*.md` as step 7 of §4.3. Stage files can be written in parallel with the stage-tracking install (step 6) since they don't depend on anything except the state machine being defined.

