# Vexa Delivery — Governing Reference

> The delivery constitution — sibling of [`ARCHITECTURE.md`](ARCHITECTURE.md). That book governs
> **how the software is built**; this one governs **how work is chosen, prepared, proven, and
> shipped** — roadmap, issues, PRs, release. Same discipline: every principle names the practice
> it comes from *and* the gate that enforces it. **A process rule that isn't enforced is a
> comment, not a rule** (D1). Gates not yet built are marked **TO BUILD** — honestly, the same
> way the architecture book ships green-on-empty gates — and tracked in
> [`DELIVERY-COMPLIANCE.md`](DELIVERY-COMPLIANCE.md).

---

## 0. The axiom

**D0 — The scarce input is verified truth about value, not code.** Coding agents made writing
code cheap; what remains scarce — and therefore what this whole system optimizes for — is
*evidence that a change delivers its intended value in the real world*. We spend our effort
preparing work so that the scarce thing (human, instrumented validation) is the only thing we ask
for. · *Source:* the economics shift of agentic coding; Lean "build quality in" (Poppendieck).
· *Gate:* this constitution is the mechanism.

## 1. The Loop — the shape of the whole system

Everything below is one closed loop. Read it once and you know the system; each principle governs
one arc, and each arc has a gate.

```
        ┌──────────────────────── THE WORLD ────────────────────────┐
        │  reports · incidents · Discord · support · probe results  │
        └───────────────────────────┬────────────────────────────────┘
                                    │  raw signal
                    ┌───────────────▼───────────────┐
                    │  1. INTAKE            (D2b)   │  ≤3 days: no signal dead-ends
                    └───────────────┬───────────────┘
                    ┌───────────────▼───────────────┐
                    │  2. PREPARE  (D-R1/2 · D5/6 · │  isolate → a ready, harnessed,
                    │     D10 acceptance floor)     │  merge-guaranteed issue
                    └───────────────┬───────────────┘
                                    │  seeded, in priority order
                    ┌───────────────▼───────────────┐
                    │  3. ROADMAP   (D2 · D3 · D-R3)│  one ordered queue, three tiers
                    └───────────────┬───────────────┘
                                    │  pick from the top / self-propose
                    ┌───────────────▼───────────────┐
                    │  4. CLAIM     (D14b)          │  heartbeat lease: 4h, renew or release
                    └───────────────┬───────────────┘
                    ┌───────────────▼───────────────┐
                    │  5. DELIVER (D-A · D6b · D-A2)│  human + agent in the harnessed loop
                    └───────────────┬───────────────┘
                                    │  a PR: value bundle + diff
                    ┌───────────────▼───────────────┐
                    │  6. PROVE (D8 · D9 · D10 · D12│  non-author witnesses value; channels
                    │     ) + SECURE (D-S)          │  corroborate; diff passes review+security
                    └───────────────┬───────────────┘
                                    │  value-signed + acceptance floor met
                    ┌───────────────▼───────────────┐
                    │  7. RELEASE   (D15)           │  batch signed PRs → ship → credit signers
                    └───────────────┬───────────────┘
                                    │  shipped value
        ┌───────────────────────────▼────────────────────────────────┐
        │  8. CLOSE BACK (D16): your report → this fix → this        │
        │     release; come validate                                  │
        └───────────────────────────┬────────────────────────────────┘
                                    └──► back to THE WORLD

   Off-loop but first-class: INVALIDATED (D11) — honest-negative of a delivery ·
   DECLINE (D17) — honest-negative of a proposal or stale item · both are results, recorded.
```

**In one sentence:** the world's feedback becomes a prepared, harnessed, merge-guaranteed issue on
an ordered roadmap; a human takes it on a heartbeat lease, delivers it with an agent in the
harnessed loop, and a *different* human witnesses the value — corroborated by instruments, never
on authority alone — after which it ships in a batch and the loop closes back to whoever raised it.

## 2. Meta

**D1 — Enforced, not aspirational.** Every rule here is machine-checked (a required status, a
checks bot, a template gate) or it does not exist; a rule that lives only in prose rots exactly as
an architecture boundary does. Unbuilt checks are marked TO BUILD and tracked — never silently
assumed. · *Source:* fitness functions (Ford/Parsons/Kua); the mirror of architecture P9.
· *Gate:* [`DELIVERY-COMPLIANCE.md`](DELIVERY-COMPLIANCE.md) — the live principle→gate→status map.

## 3. The bridge to architecture

**D-A — Delivery inherits architecture; the issue reuses the modular software.** The architecture
constitution is the *substrate* every issue is built on. An issue's atoms ARE the architecture's
isolated units (a module behind a contract, a seam), and their early validation IS that unit's
existing harness + fixtures. We do not invent test scaffolding per issue; we compose the harnesses
architecture already mandates. When an issue needs a harness that doesn't exist, building it is
architecture debt surfaced by delivery (feeds D7). · *Source:* build discipline (P-book) and
delivery discipline (D-book) share one modular substrate; testability-as-architecture (Feathers;
Humble–Farley). · *Gate:* every component's `Target:` resolves to a real module/seam and its early
validation runs in that module's own harness lane — not a bespoke script.

**D-A2 — Fixtures are first-class: every seam carries a range to play with.** Validation is built
on fixtures, produced and reused wherever possible, so every seam has a *range* of inputs — not
one happy path. Three kinds, all welcome: **deterministic producers** where raw input must be
simulated (a generator with a known oracle — e.g. a 1..500 counting fixture, where any drop, dup,
or misattribution is arithmetic); **captured live output treated as a dataset** (real DOM
snapshots, real transcripts — sanitized, versioned; a live validation that isn't captured is a
fixture wasted); **hand-authored edge fixtures** (the broken states a report described). A seam
without a fixture range is under-harnessed; adding the range is part of the work. · *Source:*
golden/contract testing (architecture P8); property-based + example-based testing. · *Gate:* each
component's early validation names its fixture(s); new seams ship a range, not a single case.

## 4. Roles and phases

**D-R0 — Two species: CONTRIBUTOR and MAINTAINER; the maintainer holds exactly two exclusive
authorities.** A **maintainer** exclusively (1) **approves an issue as `ready`** — the stamp that
puts it on the guaranteed path — and (2) **merges PRs** — checks the bundle against the acceptance
table + the closing security bundle and honors the promise. **Everything else is species-neutral**:
preparing, proposing, claiming, delivering, heartbeating, validating as the non-author, signing,
authoring probes — contributors and maintainers do all of it under identical rules ("ours
included"). Why exactly these two: `ready` is where the project's guarantee is *issued*, merge is
where it is *honored* — a promise needs an accountable guarantor; everything before and after
stays open. · *Source:* the open-source commit-bit model, minimized. · *Gate:* branch protection
(merge rights) + the `state: ready` transition restricted to maintainers; every other transition
open.

**D-R1 — Two phases, one public constitution: PREPARE, then DELIVER.** **PREPARE** — raw input is
*isolated and shaped* into a ready-to-go issue whose body is governed by these principles (the
issue body IS the constitution applied). **DELIVER** — a prepared issue is built by human + agent
in the harnessed loop and proven. Because this constitution is public, both phases are open to
both species; only the `ready` stamp and the merge are maintainer acts. · *Source:* dual-track
discovery/delivery (Cagan); open governance. · *Gate:* the state machine — `incoming → prepared`
is PREPARE (anyone); `prepared → ready` is the maintainer stamp; `ready → claimed → value-signed`
is DELIVER (anyone); `value-signed → merged` is the maintainer honoring the promise.

**D-R2 — The preparation function: prepare issues and keep the roadmap true.** Preparation is
species-neutral; only the `ready` stamp is a maintainer act. The function's job: (a) turn raw
input into prepared, isolated, harnessed issues within the intake SLA (D2b); (b) seed them onto
the roadmap in priority order (D2); (c) keep the roadmap a true picture — every known problem
represented, nothing stale asserted as ready. It is the first step before any dev starts, and the
highest-leverage work: a well-prepared issue makes delivery a known motion instead of a research
project. · *Source:* replenishment as a first-class activity (Kanban). · *Gate:* issues reach
`ready` only in full D5/D6/D10 shape.

**D-R3 — The roadmap holds three tiers; contributors pick or propose.** (1) **prepared issues** —
ready to pick, merge guaranteed by their acceptance floor; (2) **generic/raw issues** — incoming,
inside the 3-day SLA; (3) **declared items without issues** — intents too far out to prepare,
explicitly marked so the picture stays complete without pretending readiness. A contributor either
**takes a prepared item** (the guaranteed path) or **self-proposes** — welcome, but with **no merge
guarantee** until it meets the same bars. · *Source:* now/next/later roadmapping; pull with an
explicit replenishment boundary. · *Gate:* board fields distinguish the tiers; self-proposed PRs
are judged on the same value + security bars.

## 5. The roadmap

**D2 — The roadmap is one ordered pickup queue.** Every problem we know and every capability we
intend maps to exactly one ordered item; anyone takes the highest ready item. No hidden backlog,
no parallel priorities. · *Source:* single-queue pull (Kanban, Anderson). · *Gate:* the GitHub
Project board is the single source; a coverage check maps every failure mode + committed feature
to an item. **[TO BUILD: coverage check]**

**D2b — Coverage is a promise with an SLA: world feedback becomes a ready bundle in 3 days.**
Every incoming signal is triaged and converted into a prepared issue within **3 days**, then
seeded onto the roadmap. Nothing we know stays unrepresented; no report dead-ends as a raw ticket.
(`state: needs-info` pauses the clock when only the reporter can unblock.) · *Source:* lead-time
SLA / class-of-service (Kanban). · *Gate:* intake bot ages `state: incoming` items; >3 days
without `prepared` alarms. **[TO BUILD: intake bot]**

**D3 — Every item carries business meaning.** A tracker entry states what a user gains or loses,
in plain language, before any mechanism — named by the problem, opened with dry, factual stakes.
No jargon titles, no drama. · *Source:* jobs-to-be-done (Christensen). · *Gate:* title +
"why this matters" check at preparation review.

**D4 — Grounded in the code as it is.** Items are shaped by the *current* module tree and its
contracts, never by mechanism-narratives reconstructed from old reports; old reports contribute
the symptom only. · *Source:* the code is the truth (Feathers). · *Gate:* every component's
`Target:` names a module/seam that exists in the tree.

## 6. The issue

**D5 — Atomicity is two-level: the issue is the atom of VALUE; its components are atoms of CODE.**
The *issue* is the smallest holistic thing a human recognizes as "this delivers value to me" — the
smallest complete unit of perceived value, which is what gives a contributor the incentive to take
it and a clear idea of what they're validating. The *components* are isolated, harnessed code
atoms — each exactly ONE module or seam (named in a `Target:` line), each with its own
fixture/golden lane — composing into the issue's one value. A solution that needs two modules is
two components. · *Source:* minimum marketable feature (Denne–Cleland-Huang); information hiding
(Parnas). · *Gate:* issue template requires the one-sentence value statement + components each
with a real Target and harness.

**D5b — Bundle by diff, not by theme: one code change = one issue, however many values it
carries.** Issues are deduplicated by the CODE CHANGE, never by topic. When one change delivers
several recognizable values (one root cause behind several reports, one seam fix that closes
several asks), they ride ONE issue: every value is stated in its own value sentence, and the
acceptance table carries a discriminating row — and a preferred validator — *per value*, so no
value is silently absorbed into another's. When values require different changes, they stay
separate issues no matter how adjacent — relatedness is recorded as a `same-setup` note (a
claim-together recommendation on the board), never a merged item. The two-way test at
preparation: *would splitting duplicate the same diff across issues?* → bundle; *would bundling
staple independent diffs into one PR?* → split. This is D8's precondition: issue=PR one-to-one
only works if the issue is shaped like exactly one change. · *Source:* single responsibility
applied to work items — one reason to change; cohesion/coupling (Constantine). · *Gate:*
preparation review runs the split/bundle test; the acceptance-table check requires one
discriminating row per stated value.

**D6 — Preparation is ours; validation is theirs.** Every issue ships good solutions AND the
along-the-way forks — mechanism, files, steps, the branches a contributor may hit — so delivery is
a known motion, not a research project. The contribution asked for is the validation, not the
invention; alternate solutions are welcome, never required. · *Source:* "make the change easy,
then make the easy change" (Beck); paved paths. · *Gate:* prepared-solution + along-the-way
sections required before `ready`.

**D6b — The delivery motion: human + agent, harnessed loop, PR.** An issue is delivered by a human
who starts it with a capable coding agent, drives the change inside the issue's harnessed
validation loop (fixtures + early checks give fast, honest feedback), and emerges with a PR whose
bundle is the record of that loop. The harness does the mechanical proving; the human does the
recognizing. · *Source:* fast-feedback inner loop (Humble–Farley); human-in-the-loop only where
judgment is required. · *Gate:* the PR bundle shows the loop was run (per-component early-validation
observations), not just a final green.

**D7 — Every defect indicts a principle or founds one.** Each fix names the architecture principle
it restores (verbatim) and the gate that should have caught it; a defect covered by no principle
is a constitution finding that feeds back into `ARCHITECTURE.md`. · *Source:* five-whys to
systemic cause (Toyota); the two books talk to each other. · *Gate:* "Principle check" section on
fix-requests.

## 7. Proof — PRs and validation

**D8 — Issue = PR, one to one; a PR carries two artifacts — the value bundle and the diff.** The
bundle answers *"is the value real?"* (D9/D10); the diff answers *"is it correct and safe?"*
(review + D-S). They are judged on different axes and neither substitutes for the other. A diff
with no observation bundle is not reviewable, whatever it says. · *Source:* evidence-based review
("show your work"). · *Gate:* PR template requires the bundle; value gate checks the bundle,
review + security gates check the diff.

**D-S — Security is a required lane on the diff, on both sides.** Value never buys a security
pass. The contributor runs the security checks the issue names (dependency + licence scan —
architecture P17 — secrets scan, SAST where it applies) and shows them in the PR; the maintainer
runs the closing security bundle before a change enters a release. · *Source:* shift-left
DevSecOps; defence in depth. · *Gate:* security-checks required status on the PR + a maintainer
security bundle before release. **[TO BUILD: contributor security status]**

**D9 — Human validation is an instrument, spent only on the reading machines can't take —
cross-checked, never sovereign.** The human supplies one irreducible signal: *"this makes sense to
me — I witness the value."* Everything measurable is captured by machine alongside it — the
validation is **multichannel**: what the bot did (logs, FSM, egress), what the user saw (the
eyeball, screenshots, transcript), what the instruments recorded (counters, test output). The
validator is any competent non-author — a maintainer, another contributor, or the originating
reporter, who is the *preferred* signer for a fix that closes their own report. **Humans mistake,
so the human's observation carries no distinctive authority — it is harnessed in the same paradigm
as every other channel**: a green PR requires the channels to *corroborate*, not the human to
*assert*, and a human/instrument divergence is a first-class finding that blocks merge until
reconciled. · *Source:* triangulation / converging evidence; segregation of duties; no single
oracle. · *Gate:* `gate:value-signed` — green only on a non-author attestation whose multichannel
bundle is internally consistent. **[TO BUILD: value-gate bot]**

**D10 — Acceptance is a pre-declared experiment that guarantees merge — a floor, never a ceiling
on value.** Each issue publishes observations that, if presented, **guarantee** the PR merges — a
promise, not a hurdle. Every required observation is **discriminating** (a red→green pair, not
just green), **controlled** (a negative control shown red — no green-on-empty), **anchored**
(shas, ids, timestamps), and **complete** (no-regression rows). We may require some experiments
and propose others — but we **never forbid** a contributor from defining value the issue missed:
value is ultimately human-witnessed, and extending it is welcome and credited, never scope creep.
If a bundle satisfies the table and the PR is still wrong, the table was wrong — our bug, not the
contributor's (the plan-bug rule). · *Source:* ATDD; design-of-experiments controls; emergent
requirements (Beck). · *Gate:* acceptance table required before `ready`; the checks bot verifies
the bundle covers the floor. **[TO BUILD: bundle checker]**

**D11 — Both outcomes are knowledge.** A contributor who follows the prepared path and finds it
does NOT deliver has produced a first-class result: a signed INVALIDATED bundle kills a wrong
theory with evidence, credited identically to a confirmation. There is no failure state for an
honest validator. · *Source:* falsification (Popper); blameless culture. · *Gate:* verdict field
CONFIRMED/INVALIDATED; both close-or-advance the issue and appear in release notes.

**D12 — Validate at the altitude of the claim, no higher.** Unit/golden for a seam, live for a
behavior; live bars scale to the observation — speaker behavior needs 2–5 people, join/API needs
one operator, a parser needs none. Never ask for more humans than the observation requires.
· *Source:* the test pyramid (Cohn); architecture P19's runtime twin. · *Gate:* per-component
early validation at module altitude; the live bar stated and scaled in the issue.

## 8. People

**D13 — Humans author; tools assist. Disclosure welcome, co-authorship never.** What ships carries
a human name and a human's full responsibility — the sole author is the human, and responsibility
is honored as full authorship: full credit, full standing. "The agent wrote it" is neither a
defense nor a discount. Disclosing your tooling in the PR is welcome as transparency, never
required, and never an attribution: an agent is not a co-author, gets no `Co-Authored-By` trailer,
holds no standing. Tools are instruments; instruments don't sign. · *Source:* engineering
accountability; provenance without diffused responsibility. · *Gate:* commit-trailer check rejects
agent co-authors; the attestation signer is the accountable author. **[TO BUILD: trailer check]**

**D14 — The tracker is the CI of the human loop.** Labels are truth, not decoration: a state label
means exactly what it says, enforced by the state machine (`incoming → prepared → ready → claimed
→ awaiting-evaluation → value-signed → closed-with-release`). Nothing is claimed before `ready`;
community-authored issues enter the same machine. · *Source:* explicit value stream (Lean);
make-illegal-states-unrepresentable (Wlaschin). · *Gate:* a label bot enforces legal transitions;
`ready` is the maintainer stamp. **[TO BUILD: label bot]**

**D14b — A claim is a heartbeat lease, not ownership.** Anyone claims a `ready` item — no
permission needed. A checkout is a **4-hour lease**; a **heartbeat** (a short "here's what's going
on" update) renews it for another 4 hours. No heartbeat → the lease expires and the item returns
to `ready`, claimable by anyone; the prior holder may reclaim with a fresh heartbeat. Work is
never hoarded and flow never stalls on an absent contributor — and the heartbeats become the front
of the PR's observation bundle, the loop's narration written as it happens. · *Source:* TTL leases
(distributed systems); WIP-pull with abandonment; work-stealing. · *Gate:* a lease bot stamps
`claimed` + expiry, watches heartbeats, auto-releases on a miss — all on the issue timeline.
**[TO BUILD: lease bot]**

### 8b. The tags — the state machine's alphabet

Five orthogonal dimensions; a tag is a claim, and the bots keep every claim true. **Exactly one
`state:`, exactly one `kind:` on prepared work, any number of `area:`, `P0` only when
production-critical, `good-first` only when truly zero-prerequisite.**

| Dimension | Tags | Rule |
|---|---|---|
| `state:` | `incoming` · `needs-info` · `prepared` · `ready` · `claimed` · `awaiting-evaluation` · `value-signed` | exactly one, always; transitions only via the machine; `ready` = maintainer stamp; `claimed` = live lease; closure carries no state |
| `declined:` | `out-of-scope` · `superseded` · `wont-fix` · `too-far` · `stale` | exactly one, on closed-without-merge only (D17) |
| `kind:` | `fix-request` · `evaluation-request` · `probe` | the prepared ask's contract shape; legacy `type: bug\|feature\|docs` stays as the raw signal's nature — an issue carries both |
| `area:` | existing taxonomy + `security` | routing; any number |
| flags | `P0` · `good-first` | strict meanings; never inflate |

Invariants the bots enforce: one `state:` at all times; illegal transitions reverted; `ready`
maintainer-only on a passing template check; `claimed` only with a live lease; a PR merges only if
its issue is `value-signed` + security green; `incoming` >3 days alarms; `needs-info` quiet 14
days → proposed `declined: stale`; every closed-without-merge issue carries one `declined:` reason.

## 9. Release and closure

**D15 — Release closes the loop, fast.** A maintainer batches a few value-signed PRs, runs the
release machinery (image set, gates, VM-validated) and the closing security bundle, ships, and the
notes credit every signer — including INVALIDATED ones. The release loop and the contribution loop
are the same loop. · *Source:* small batches, fast flow (Accelerate/DORA); continuous delivery.
· *Gate:* release-set + `release/vm-validated` + maintainer-security-bundle required statuses;
notes generated from signed bundles.

**D16 — The loop closes back to its origin.** When a change ships, the report that seeded it is
told — "your report → this fix → this release" — and the reporter is invited to the validation. A
fix that ships without closing back leaves the loop open: the person who gave us the truth never
learns it landed, and never becomes the repeat contributor they were about to be. · *Source:*
close the feedback loop (Deming); the flywheel. · *Gate:* release notes link origin reports; a
ship-closes-report step comments on the originating issue. **[TO BUILD: close-back step]**

**D17 — Decline is a first-class outcome; nothing rots.** Not everything is taken forward, and a
graceful, reasoned *no* is part of the system: a self-proposed PR not accepted, a prepared issue
unclaimed too long, a report out of scope — each closed with one stated `declined:` reason,
recorded and auditable. INVALIDATED is the honest-negative of a *delivery*; decline is the
honest-negative of a *proposal or a stale item* — both are results. A silent-rot backlog is the
failure state we refuse. · *Source:* explicit disposition; stop starting, start finishing (Lean).
· *Gate:* every closed-without-merge issue carries a `declined:` reason; intake/stale bots propose
declines rather than letting items age. **[TO BUILD: stale proposer]**

---

## 10. How this document changes

Like the architecture book: propose the change, record the decision as an ADR under
[`adr/`](adr/) (process decisions live beside build decisions), and update the compliance map. A
principle without its gate enters as TO BUILD, never as silently assumed.
