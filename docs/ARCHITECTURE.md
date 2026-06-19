# Vexa Architecture — Governing Reference

> The constitution. Before you add a file, move a module, or define a contract, it is
> governed by something here. **Every principle names the established practice it comes from
> *and* the CI gate that enforces it** — so no rule lives by convention alone. If a rule is not
> gated, it is aspirational; treat closing that gap as work.
>
> Scope note: `crm` and `retrieval` are **deferred**, and there is no `memory` domain. A workspace
> is a *user-owned git repo* (data, not platform code); `crm` is an *application* — one entity schema
> over a workspace (see **P11**) — not a platform domain. This doc governs what we build now.

---

## 0. The shape, in one sentence

Vexa is **contract-bounded at two scales** — a handful of **microservices** coupled only by published
schemas, each internally a **modular monolith** of modules coupled only by ports — all over a shared
**runtime kernel**. The construction discipline is modular-monolith; the deployment shape is
microservices, carved where a real force requires it (runtime, scale, data, ephemerality).

---

## 1. Concepts — the vocabulary (use these words precisely)

| Term | What it is | In Vexa | Canonical source |
|---|---|---|---|
| **Module** | unit of *composition* — a library behind a contract; compile-time, no runtime of its own | `@vexa/*` bricks under a domain's `modules/` | Parnas (*information hiding*, 1972); Szyperski (*Component Software*) |
| **Service** | unit of *deployment* — a process with a lifecycle and an address; runtime | a dir with an entrypoint (bot, meeting-api, agent-api) | C4 "container" (Brown) |
| **Domain** | a *bounded context* — one cohesive concern with its own language | `runtime/ meetings/ agent/ identity/ …` | DDD (Evans) |
| **Contract** | the *only* sanctioned coupling between two parties | ports (in-process) + schemas (at boundaries) | Design by Contract (Meyer); Published Language (DDD) |
| **Port** | an interface the core depends on — a "hole" an adapter fills | `JoinDriver`, `Pipeline`, `TranscriptSink` | Hexagonal / Ports & Adapters (Cockburn) |
| **Adapter** | binds a port to a real transport or external brick | `join-vexa`, `transcript-redis`, `lifecycle-http` | Hexagonal; Anti-Corruption Layer (DDD) |
| **Published schema** | a *language-neutral* contract at a boundary | `contracts/*.v1` (JSON Schema / OpenAPI + golden vectors) | schema-first / consumer-driven contract testing |
| **Kernel (runtime)** | the domain-agnostic execution substrate everything sits on | `runtime/` (spawn/execute, now or scheduled; mounts the workspace) | platform substrate |
| **Workspace** | a *user-owned git repo* — durable memory the agent reads/writes; **data, not platform code** | the user's repo; template = `agent/contracts/workspace.v1`; mount = a `runtime` capability | git-as-database; mechanism-not-policy |
| **Composition root** | the one place wiring happens; the only place adapters meet the core | a service's `index.ts` / `main` | DI composition root (Seemann) |
| **Worker** | an ephemeral, stateless service spawned on demand and disposed | `bot` (per meeting), `agent` (per run) | 12-Factor (disposability) |

---

## 2. Principles — the rules (each has a *why*, a *source*, and a *gate*)

| # | Principle | Why | Source | Enforced by |
|---|---|---|---|---|
| **P1** | **Package by domain, not by layer.** Top-level dirs name the business (`meetings`, `agent`), never the framework (`controllers`, `utils`). | the structure should scream what the system *does* | Screaming Architecture (Martin) | review + structure |
| **P2** | **Couple only through contracts.** No import reaches around a contract into another module's internals. | a boundary you can't reach around can't rot | Information hiding (Parnas); Bounded Context (DDD) | `gate:isolation` |
| **P3** | **Dependencies point inward to the kernel; the graph is acyclic.** `runtime` depends on nothing above it. No cycles, ever. | the core must never depend on the edges; a cycle is mud | Clean / Onion architecture (Martin, Palermo) | `gate:graph` |
| **P4** | **Cross a language or domain boundary → a published schema. Stay intra-domain and in-process → nest the contract with its owner.** A contract that crosses a **process / network / independently-deployable boundary is sealed + versioned + golden-pinned even when both sides are the same language and domain** — only a truly in-process, same-artifact call nests as a bare port. | the Python consumer can't `import` a TS type; *and* two sides that deploy independently (extension↔desktop, bot↔desktop) drift silently across a same-language wire unless it's pinned — `capture.v1` is the busiest such wire | schema-first / contract testing; independent deployability (Newman) | `gate:schema` · `gate:contract-version` (every cross-process `.v1`) |
| **P5** | **Adapt at every boundary someone else owns.** Their vocabulary is translated at the edge, never leaked into your core. | one brick's churn must not ripple into your logic | Hexagonal + Anti-Corruption Layer (Cockburn, Evans) | review |
| **P6** | **One public front door per module; internals are private.** Consumers import the `index`, never a deep path. | you can refactor freely behind a stable surface | encapsulation / information hiding | `gate:exports` |
| **P7** | **Workers are stateless and ephemeral; all config arrives by env.** They spawn, work, emit, and die. | horizontal fan-out (one bot per meeting) + disposability | 12-Factor (Wiggins) | review |
| **P8** | **The goldens are the spec.** A contract's truth is its committed example vectors, not the current output of any implementation. | stops "fix the test to match the bug" | golden/contract testing; fitness functions | `gate:schema-conformance`, `gate:unit` |
| **P9** | **Every boundary is mechanically enforced, not aspirational.** A rule in a README rots; a rule that turns CI red cannot be crossed. | this is the meta-principle that keeps a modular monolith from decaying | Evolutionary Architecture / fitness functions (Ford, Parsons, Kua) | all gates |
| **P10** | **Default to a module; carve a service only when a force requires it** (independent scale, different runtime, separate team, hard fault isolation). | distribution is a tax — pay it on purpose, not by reflex | Modular Monolith (Brown) | review |
| **P11** | **Mechanism, not policy.** The platform owns *mechanism* (`runtime`, contracts, the workspace primitive); a *specific* entity schema, integration, or a customer's workspace is **config at the edge — never a platform domain.** | a sales CRM schema and a bank's control catalog are both just schemas; freeze one into the platform and it fits no one else | mechanism-not-policy (microkernel tradition); Open/Closed | review + structure |
| **P12** | **Every folder self-documents.** A directory at any level carries a `README.md` stating its one concern, its public surface (the `index`/contract it exposes, or the children it groups), and what it may depend on. Trivial leaves get a one-liner — the rule is *existence*, not length. | a modular tree must be navigable; the README is the front-door *doc* beside the front-door *code* (P6) | self-documenting systems; the `modules/README` symptom→brick router | `gate:readme` |
| **P13** | **Language minimalism.** Add a language only when an ecosystem forces it (browser→TS, ML→Python); the control plane's language is a deliberate choice. Align every language boundary with a service+contract boundary — never mix languages within a module. | each language multiplies the schema surface | mechanism-not-policy; P10 for languages | review · ADR-0001 |
| **P14** | **Config is a validated contract, delivered by env.** App vars are `VEXA_*`; structured config travels as one JSON env var validated against a `*.v1` schema; secrets are a class (`*_TOKEN`/`_SECRET`/`_KEY`) — never logged, committed, or in goldens; validate at boot, fail fast. | env is where config discipline usually leaks | 12-Factor; schema-first | `.env.example` · ADR-0002 |
| **P15** | **User data & secrets are protected by default.** Data → per-tenant envelope encryption (crypto-shreddable, BYOK); secrets → a vault behind a port; the agent gets scoped, brokered, audited access — never raw keys in its workspace or logs. | a meeting product's data is its liability | data-protection; least privilege | ports now, impl deferred · ADR-0003 |
| **P16** | **Defer the implementation, not the seam.** A deferred capability is a port with a default (passthrough) adapter, wired through now; the contract *fields* it needs are added **additively** when it lands (optional fields are back-compatible — so early threading buys nothing). | "plug-and-play later" only works if the socket exists now; but unused fields are noise | open/closed; ports & adapters; YAGNI | review · ADR-0003 |
| **P17** | **Every dependency is OSS-licence-clean.** Direct *and transitive* deps carry an OSI-approved permissive licence (Cat A: Apache-2.0 / MIT / BSD / ISC / …); weak-copyleft (Cat B: MPL / EPL / LGPL) only when isolated (unmodified, not statically bundled) and exception-logged; strong-copyleft (GPL / AGPL) and source-available/proprietary (BSL / SSPL / Elastic / Commons-Clause) are **forbidden**. The platform must drop into a regulated org with zero licence encumbrance. | one GPL/AGPL or source-available dep *anywhere* in the tree blocks deployment in a bank — the licence tree is a hard deployment constraint, not a footnote | FINOS OSS governance; ASF licence categories A/B/X; SBOM/SPDX | `gate:licenses` · ADR-0004 |
| **P18** | **Fail loud and attributable.** A dependency's failure is translated at its adapter into a **typed fault** (`source` + `kind`) and surfaced on an **observable channel** (log · telemetry · a health frame · a lifecycle event) — never swallowed into silent degradation. A running component also exposes its **health** (can it reach its dependencies?) and **liveness** (is the expected signal actually flowing?) — **absence of an expected signal is itself a reportable state.** "No output" must be distinguishable from "the dependency is down / unpaid / unauthorized" *and* from "nothing is arriving." | a silent *fault* read as "the extension is broken" (STT `402`); *and* a silent *absence* — "session active, zero audio frames" (the YouTube stream-not-minted case) — throws nothing yet looks identical to "no speech" | crash-only / fail-fast (Candea–Fox); observability (Majors); health checks + absence-of-signal (Google SRE) | `onError` seam + fault-surfacing gate · `/health` + no-frames watchdog (ADD) · ADR-0010 |
| **P19** | **Prove at the altitude of the claim.** A capability is "done" only when proven at the level it operates: a user-facing *behavior* needs live evidence (L4), not just structural/contract green (L1–L3). State *which* level a "green" claim rests on; the proof obligation scales with the claim's blast radius. | the costliest failures hide behind L1–L3 green read as "works" — a lane marked done while gmeet was untested, YouTube intermittent, STT dead; the L1–L4 pyramid (§5) is the *mechanism*, this is the *obligation* that binds a claim to it | test pyramid (Cohn); risk-based verification; DORA | a recorded **L4 eval baseline** per user-facing lane (the `eval/` harness; `gate:eval-baseline` ADD) · ADR-0011 |
| **P20** | **Complete mediation — authorize every access, default-deny.** Every read/write of a user-owned resource passes a `canAccess(subject, resource, action)` check at **every** path (API · live subscribe · agent), defaulting to owner-only. | P15 protects data *at rest* (encryption, secrets-as-a-class) but not *who may read it* — the desktop's `/transcripts`·`/recordings`·`/ws` are wide open, and ADR-0003's `canAccess` seam was designed but never wired, so it rotted (P9) | complete mediation + least privilege (Saltzer–Schroeder); default-deny | `canAccess` port on the three read paths + a deny test (`gate:access` ADD) · ADR-0012 |
| **P21** | **Report state from evidence, not intent.** A component's displayed/reported state reflects **observed reality, not the action attempted** — a success/active status is *earned* by the confirming signal (capture is "Listening" only once frames are observed flowing; "started, no signal" is its own state, never "working"). `started ≠ working`. **Principles & gates extend to the clients** — the extension/desktop UI is where the user meets failure, so it is in scope, not exempt. | "Listening — capturing 0 stream(s)" flips to success on the Start *command* while no audio flows — an unearned positive that hides the commonest failure exactly where the user sees it; the client was the least-governed code (zero tests) precisely where it matters most | runtime dual of P19; positive complement of P18 (don't fake success); make-illegal-states-unrepresentable (Wlaschin) | first-frame-observed transition + no-frames watchdog + client state-machine tests (`gate:client-liveness` ADD) · ADR-0013 |

---

## 3. The structure (current scope)

```
vexa/
├── runtime/        ① KERNEL — spawn/execute workloads · Docker·K8s·process · domain-agnostic
│   └── contracts/  runtime.v1
├── meetings/       ② CAPTURE — meeting-api · bot · transcription · tts · eval/ → transcript + events
│   └── contracts/  transcript.v1 · lifecycle.v1 · acts.v1 · invocation.v1
├── agent/          ③ EXECUTION — agent-api · sandboxed worker (scoped identity + a mounted workspace)
│   └── contracts/  workspace.v1
├── identity/       access · accounts · tokens · audit — authN/authZ   (+ rest-api · webhook contracts when built)
├── gateway/        the edge — auth · routing · WS fan-out
├── integrations/   out/ (FINOS adapters, on the agent emit port) · in/ (calendar → scheduler)   [email/github deferred]
├── clients/        dashboard · extension · desktop · telegram · mcp
├── sdks/           vexa-client · vexa-cli · transcript-rendering
├── tools/ · deploy/ · docs/
├── package.json · pnpm-workspace.yaml · turbo.json    ← workspace root
└── .github/workflows/gates.yml
# Contracts NEST with their owner domain (no top-level schemas/). Language-neutrality is the FORMAT
# (JSON Schema, read by path), not the location — so each domain stays self-contained and liftable.
# A workspace is a USER git repo (data, not in this tree); template = agent/contracts/workspace.v1.
# deferred (NOT platform domains): crm (an app over a workspace, P11) · retrieval (a vector+KG service)
```

**Dependency rules** (the `gate:graph` spec — acyclic):

```
A domain's INTERNALS (services/, modules/) may import: its own code · another domain's contracts/
(the published seam) · runtime/contracts. They may NOT import another domain's internals.

runtime internals  → (nothing above; owns runtime.v1)
meetings internals → runtime/contracts · its own contracts
agent internals    → runtime/contracts · meetings/contracts (consumes transcript.v1) · its own
identity · gateway → contracts only (gateway routes over HTTP, imports no internals)
clients · sdks     → contracts (+ sdks)

★ meetings ⊥ agent at the INTERNALS level. agent MAY reference meetings/contracts/transcript.v1
  (that IS the seam); it may never import meetings/services or meetings/modules.
```

**Contract placement** (P4 applied): a contract **nests with its owner domain** in `<domain>/contracts/`
as JSON Schema — `runtime.v1`→runtime, `transcript/lifecycle/acts/invocation.v1`→meetings,
`workspace.v1`→agent. Cross-language is satisfied by the *format* (JSON Schema, read by path), **not** a
shared location — so domains stay self-contained and liftable. Purely in-process, TS-to-TS brick contracts
(e.g. `capture.v1`) still nest as `.ts` inside the owning module's `src/contracts/`.

---

## 4. The gates (CI — the teeth)

Each gate enforces one or more principles. **An artifact "exists" only when it is gate-green** —
*"verified-compliant" = passing this suite.* Admit nothing on trust. ("ADD" = gap to close for 0.12.)

| Gate | Checks | Enforces | Tool | Status |
|---|---|---|---|---|
| `gate:isolation` | every import is intra-module, builtin, or a declared dep | P2 | `check-isolation.js` | **have** |
| `gate:graph` | module graph is acyclic + matches the allowed-edges spec | P3 | `dependency-cruiser` | **have** (green-on-empty; bites as packages land) |
| `gate:exports` | no consumer deep-imports past a module's `index` | P6 | `package.json` `"exports"` + scan | **have** (locks land per-package) |
| `gate:readme` | every non-ignored directory has a non-empty `README.md` | P12 | tree-walk | **have** |
| `gate:schema` | goldens ≡ schema (ajv) | P4, P8 | `validate.mjs` (ajv) | **have** (both-language conformance per-consumer in Stage 3/4) |
| `gate:contract-version` | a sealed `.vN` schema is frozen; any change routes to human re-seal (back-compat) or a vN+1 dir (breaking) | P4 | seal hash (`contracts.seal.json`) | **have** |
| `gate:unit` | per-module tests pass (the L1–L2 pyramid) | P8 | `npm test` per package | **have** |
| `gate:e2e` | offline lane/wire integration (L3) | P8 | desktop/bot e2e | **have** |
| `gate:fault-surfacing` | a forced dependency fault (e.g. STT `402`) is surfaced + attributed via `onError`, never swallowed | P18 | failure-injection tests (under `gate:node`) | **have** |
| `gate:health` | each long-running service exposes `/health` (deps reachable) + a liveness watchdog (no expected signal for N s → reported) | P18 | per-service | **add** |
| `gate:eval-baseline` | each user-facing lane carries a recorded **L4** eval artifact ≥ baseline before it is "done" | P19 | `eval/` harness | **add** |
| `gate:access` | each read path (API · WS subscribe · agent) denies an unauthorized `canAccess` request | P20 | deny test | **add** |
| `gate:client-liveness` | the extension's capture state is **evidence-driven** — "active" only after first-frame-observed; a no-frames watchdog flips to "no-signal"; the state machine is unit-tested | P21 | extension L2 tests | **add** |
| `gate:licenses` | every direct+transitive dep licence is on the allowlist (Cat A; B by logged exception); no GPL/AGPL/source-available; emits an SBOM | P17 | `license-checker` (npm) · `pip-licenses` (py) · SPDX | **have** |
| `typecheck` / `gate:standalone` | `tsc` clean against own declared deps | P2 | `tsc --noEmit` | **have** |
| `gate:dist-in-sync` | committed `dist/` ≡ clean rebuild of `src/` | — | — | **retire** (workspace tool builds on demand → delete committed `dist/`) |

**Two-layer enforcement.** Locally, a `pre-push` hook (`.githooks/pre-push`, wired by `core.hooksPath` via the root `prepare` script — zero-dependency, per P17) runs `pnpm gates` and blocks any push that isn't green. In CI, `gates.yml` re-runs each gate as its own step so a failure is unambiguous. `git commit` itself runs nothing — the bar is at **push**, not every commit.

---

## 5. How we prove a change — the validation pyramid

Build downward from the cheapest, most-isolated proof. The bot is the worked example (71 checks).

| Level | Proves | How | Speed |
|---|---|---|---|
| **L1 — contract** | the contract is self-consistent + the goldens conform | schema + golden vectors | ms |
| **L2 — unit** | the core logic, with every port mocked | in-memory fakes for ports | ms |
| **L3 — integration** | the real engine wired to mock externals | real lane/module + mock STT/redis | ~1s |
| **L4 — live + eval** | the whole thing against reality, **plus quality vs ground truth** | hot container, real meeting + the `eval/` harness | minutes |

Rule: a port (P5) is what lets L2 exist. If you can't unit-test the core without a browser/redis,
you're missing an adapter seam. The **eval harness** (L4 quality) is first-class — it's how a domain
proves its *output is correct*, not merely that it ran.

---

## 6. Reference shelf (read these to pressure-test a decision)

| Practice | What it governs here | Source |
|---|---|---|
| **Microservices** | the system shape: services over REST/Redis, carved by force (P10) | Newman; Fowler |
| **Modular Monolith** | each service's internal shape + the movable module↔service boundary | Simon Brown; Spring Modulith (Drotbohm) |
| **Hexagonal / Ports & Adapters** | every service's internal shape | Cockburn |
| **Clean / Onion** | dependency direction (P3) | Martin; Palermo |
| **DDD — Bounded Context, Published Language, ACL** | domains, schemas, adapters | Evans |
| **Information hiding** | why a module hides one decision behind one front door (P2, P6) | Parnas (1972) |
| **Component Software** | the module definition | Szyperski |
| **C4 model** | module (component) vs service (container) vs deploy node | Brown |
| **12-Factor** | workers + config-by-env (P7) | Wiggins |
| **Mechanism not policy** | the platform stays generic; schemas are config (P11) | microkernel / Open-Closed |
| **Evolutionary Architecture / fitness functions** | gates as executable architecture (P9) | Ford, Parsons, Kua |
| **Screaming Architecture** | package-by-domain (P1) | Martin |

---

## 7. When the rules bend (be honest, not dogmatic)

- **An adapter is ceremony** if it does no vocabulary translation, no dispatch, and unlocks no
  test seam, over a stable leaf — inline it (P5 has a cost).
- **Default to a module.** A new service must justify its distribution tax against P10's forces.
- **A README boundary is acceptable for a leaf with no consumers.** The moment it has two, gate it (P9).
- **`shared/`-style kernels are allowed but strict** — smallest, most stable, most reviewed code;
  never a junk drawer. If you can't name the one concern it hides, it's not a module.

---

## 8. Development process — how we build (and contribute)

The sections above say *what the system is*. This says *how you change it*. Same discipline — every
step names its practice and ends at a gate.

**The inner loop (one change):**
1. **Contract first** — define/change the port or `contracts/*.v1` *before* the code; the contract is the unit of agreement. *(API-first / consumer-driven contracts)*
2. **Implement behind a port** — transports are adapters; the core stays offline-provable. *(hexagonal)*
3. **Prove down the pyramid** — L1 golden → L2 unit (mock ports) → L3 integration → L4 live + eval. Cheapest proof first. *(test pyramid)*
4. **Green under the gates is "done"** — isolation · graph · exports · schema-conformance · unit. *Green or it didn't happen.* *(fitness functions)*
5. **Small PR on trunk** — short-lived branch, small diff, gates required to merge. *(trunk-based dev / DORA)*

**Special rules:**

| Rule | Why | Practice |
|---|---|---|
| **`lane:contract` PRs are human-gated** — a PR label that routes any change touching a `contracts/*.v1` to *required human review* (it can break consumers across languages); ordinary changes merge on green gates alone | published contracts have a wide blast radius | published-language governance; semver (add-optional = v1, breaking = bump to v2) |
| **Fix in the brick that owns the symptom** | never mask a brick's bug in a consumer | symptom→brick router (`modules/README`) |
| **Reproduce with no live meeting before you fix** | the brick's own logs are a claim, not proof | brick debug discipline |
| **Record decisions as an ADR** — *Architecture Decision Record*: a short, dated, numbered `docs/adr/NNNN.md` capturing **one** decision = context · the decision · the trade-off accepted | the durable "why," so a boundary isn't relitigated later | Nygard ADRs |
| **Big work is staged with per-stage validation gates** | each stage is specific and ends at a *runnable* proof; never advance on red | staged migration |

**The expectation–reality loop — how we run a session, and how the principle-set grows:**
1. **State the expected behaviour first.** Before acting, name what the system *should* do and what "done" looks like for the current objective — the contract for the work in front of you. *You can't detect a divergence you never defined.* *(expectation-first; P19/P21 applied to the work itself)*
2. **Match reality against it — by instrument, definitely.** Default to **instrumented, definite validation**: deterministic gates, unit/integration tests, the `eval/` `replay`·`analyze`·`benchmark` path — reproducible, no judgement call, no human. "It ran" is a claim; the instrument is the proof.
3. **The human is the highest, scarcest resource — and fallible.** Spend human validation **last and least**. When only a human can decide (real browser behaviour, real-meeting quality): **minimise** the ask; hand a **minimal, fully-instructed surface** (the exact `🧑` step, never a vague request); and **cross-validate the human — never take it as definitive.** "I topped up the balance" / "it works" is *intent*, not evidence (P21) — confirm it with an instrument (ping the service, census the tape) before relying on it. A human is one more signal that must be evidence-backed.
4. **An unexpected error is a STOP.** Reality ≠ expectation ⇒ stop. Don't paper over it, blind-retry, or push past — an unpredicted behaviour is a *signal*, not a nuisance.
5. **Root-cause every surprise to an architectural gap, and close it.** Each unexpected error is a *symptom* of a missing or violated principle. Trace it to that gap, fix the instance, and **codify the gap as a principle + its gate** so it cannot recur. *This is how the principle-set grows* — P18 (silent STT `402`), P19 (gate-green ≠ works), P20 (open read paths), P21 ("Listening" over silence) were each born from one such surprise. *(blameless root-cause; evolutionary architecture)*

> **Collapsed:** *Expect → instrument → (human: minimal, cross-validated) → stop on surprise → root-cause to a principle → codify.*

**The brick lifecycle (how a module is born):** scaffold (one template, incl. its `README.md`: *what · surface · deps*) → define its contract (a nested
port, or `contracts/*.v1` if it crosses a boundary) → implement behind the port → pass the gate suite →
*admit* it (consumers may now import its `index`). **A brick that isn't gate-green doesn't exist yet.**

**Collapsed:** *Contract → pyramid → gates → small PR. Contracts (`lane:contract`) are human-gated. Fix
in the owning brick. Decisions get an ADR. Big work is staged to runnable proofs.*

---

*This file is the source of truth for "how we build." Changes to a principle or a gate ride a
`lane:contract`, human-reviewed PR and are recorded as an ADR under `docs/adr/`.*
