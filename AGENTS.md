# AGENTS.md — the operating contract (read before you act)

This repo is governed by `**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**` — the constitution (P1–P21,
the gate suite, the development process). This file is the *front door*: how you run a session in it.

## One worktree per chat

Concurrent work is isolated: each chat works in its **own `git worktree*`* on a short-lived branch
(`git worktree add ../v0.12-<slug> -b chat/<slug>`), integrating to the integration branch via **PR**
(gates required; `lane:contract` human-gated). **Never two chats on one working tree**, and **never touch
another chat's uncommitted files** — if surprise files appear from another chat on a shared tree, surface
them, don't adopt them (ADR-0019).

## Plan first — three modes; goal vs objective

Never build without a current plan. **Planning mode** produces/revises the *full path* to the **goal**
(`[docs/RELEASE-PLAN.md](docs/RELEASE-PLAN.md)` — the always-current, staged plan); **execution mode** runs
it one **objective** at a time, *in-line*, under the loop below; **debug mode** runs the same loop as an
**orchestrator** — delegating isolated objectives to background agents/workflows to keep the chat open for the
human (next section). **Goal** = destination (end of plan = the release); **objective** = the current *go*.
You are always executing toward exactly one open objective; it closes **expected** (→ next objective,
autonomously) or **unexpected** (→ stop, interpret *with the human*, as learning → codify, re-plan). Never
drift; always in exactly one mode (ADR-0015/0017).

**What planning mode must produce — settled *before* any execution:**
- **The goal — the destination.** One **falsifiable, gate-backed** end-state (ADR-0017): the exact green the
*end of the plan* shows. Not "0.12 works" — the specific gates / L4 evidence that closes the release.
- **The hop chain to it.** The ordered objectives from here to the goal, each ending at a runnable proof,
critical path and parallel workstreams marked (the `docs/RELEASE-PLAN.md` objective chain).
- **The forks — Plan A / Plan B.** At each objective that *might not deliver*, name the decision point and
pre-stage **both** branches: *delivers → next hop; doesn't (the foreseen non-delivery) → the fallback path*.
A fork you reasoned through in planning is **EXPECTED** — you take the pre-staged branch autonomously, no
STOP. STOP stays reserved for the genuinely *un*foreseen (loop step 4). Asking "what if this doesn't
deliver?" in planning is how would-be surprises become planned branches.
- **The resolved + validated resources.** Where each hop builds and runs, which services / APIs / credentials
it needs — each **preflight-proven available**, not merely recorded (next section). An unresolved or
unvalidated resource is a **blocker to clear before the plan is approved**, never a discovery at execution.
- **The operations + their permissions.** Every action a hop will *perform* that needs a capability or
clearance — **build / rebuild an image, redeploy, restart a service, a destructive `down -v`, a DB
migration, a git push, opening a PR, sending anything outward** — named and **preflight-validated as both
*possible* AND *permitted*** on the target: the build context + base image present, the lane / human-gate
cleared, the action non-disruptive to others. **Validate the verb, not just the noun** — you can hold the STT
token yet lack the ability or permission to rebuild the stale image you'll need. An unproven operation is a
**blocker to clear before approval**, never a mid-execution scramble (Learning #31).

## Debug mode — orchestrate, delegate, keep the chat open

In **debug mode** you stop being the hands and become the **orchestrator**. The chat is a scarce, serial
resource — the one place the human steers; it must stay **open for new input and re-direction**, never
blocked on a long-running task you could have handed off. So the moment an objective is **well-understood,
scoped, and isolated** (clear inputs, a falsifiable `Expected`, no shared mutable state with other live
work), you **delegate it** to a background agent or a workflow and return to the chat — you do not sit and
watch it run.

- **Delegate as soon as it's isolatable, not before.** Think the task through *first* — its `Expected`, its
  resources/operations preflighted (the registry + preflight sections still apply to delegated work), its
  blast radius. A half-understood task handed to a background agent just relocates the confusion. **Isolation
  is the gate:** only a task that can run to its own DoD without further human steering is eligible to leave
  the chat.
- **Parallel by default when independent.** Multiple objectives with **no ordering dependency and no shared
  working tree** launch **in parallel**, not in sequence — fan them out in a single batch (`run_in_background`
  agents, or a `Workflow` when the fan-out has its own multi-stage structure). Anything with a real
  dependency stays a pipeline; never fake-parallelize work that shares state (one worktree per chat still
  holds — give parallel mutators their own isolation, ADR-0019).
- **The chat is the control plane, not the worker.** While delegates run, the chat takes new human input,
  re-prioritizes, scopes the next task. You surface only **gates and verdicts**: a delegate returns, you read
  its **Actual vs Expected**, stamp the **Verdict** in the ledger (delegation does **not** drop the loop —
  every backgrounded objective still closes EXPECTED → continue · UNEXPECTED → STOP+learn), and either close
  it or fold its result into the next hop.
- **Track every outstanding delegate.** A backgrounded objective that no one is waiting on is a dropped hop.
  Keep the open delegates visible (the task list / ledger), so the human can see what is in flight and the
  loop still fires when each returns.

> *Debug mode = think it through → isolate → delegate (parallel if independent) → keep the chat free → collect verdicts. The orchestrator never blocks on a task it could hand off.*

## Where work runs — the execution-target registry (ADR-0020)

The repo carries a **gitignored, host/user-specific** execution-target & resource registry
(`deploy/execution-targets.json`; template `…example.json`; schema `deploy/contracts/execution-targets.v1`):
the **targets** work may run on (name · arch · caps: docker·compose·amd64-bot·gpu·…) and the **resources** it
needs (services, credential-sets, storage, a meeting, a human gate) — **secrets by reference, never inline** (P14).
**In planning mode, before a plan is approved, RESOLVE *and VALIDATE* every stage's `Runs on:` + `Resources:`
against the registry, and surface any missing/unavailable/unhealthy one as a blocker to clear first** — never
enter execution on an unresolved one (Learning #22: the amd64 bot's host is `bbb`; consult the registry before
escalating a "block").

- **Resolve ≠ validate.** A resource is cleared only when a **cheap preflight PROVES it actually works** — the
endpoint reachable, the credential authenticates, the account **funded/in-balance**, the host runs what's
needed — *not* merely that it is recorded. **Surface the failure model in planning, not at execution**
(validation economy, §8.2 — cheap instrument first): a one-call STT probe in planning would have caught the
out-of-balance `402` that a 4-hour live run discovered (Learning #28). Each `Resources:` line names HOW it is
validated (the preflight check), and the registry records the *working* credential (not a stale/empty one).
- **Validate operations, not only resources.** A hop that **rebuilds / redeploys / restarts / migrates /
pushes** must, in planning, prove it both **CAN** (build context + tooling + base image present, target
reachable) and **MAY** (permitted, right lane, non-disruptive to others) — the same *resolve + validate*
discipline applied to the **verb**. A **stale deployed artifact is a foreseeable failure**: validate
image/code **currency** preflight, and if a rebuild is the fix, clear the rebuild capability + permission
**then** — not after a live run discovers the staleness (Learning #31).
- **Verify the human's VIEW binds to the target you run on.** When a human observes through a UI
(browser → dashboard → backend), confirm that view reaches the **same stack the work runs on** before
trusting a single thing they report. A **same-port localhost stack — or a silently-failed tunnel — transparently
shadows a remote target** (`ssh -L 18030` no-ops if `:18030` is already a local container). Plant a
**per-stack marker the intended backend returns** (a unique id, a `[DBG]` line that must appear in the
*target's* logs) and confirm the human's view hits it; check `lsof`/`docker ps` for a local listener before
assuming a tunnel. Hours of "fixes" went to bbb while the browser watched a local stack (Learning #34).
- Every objective in the ledger carries a `Runs on:` + `Resources:` line (and, where it acts, an
`Operations:` line). Enforced by `gate:execution-env` (the registry conforms) + the planning preflight
(each resource's validation check **and each operation's can/may check** passes).

## The architecture chart — read it first (`architecture.calm.json`, P23)

The runtime **data-flow + ownership** model (FINOS CALM) is the **index for AI and the mental model for
humans**: every service / module / contract / client is a node; every redis stream · pubsub · table · blob
is a *data carrier* with **exactly one writer**; `connects` edges carry the governing contract (the *shape*)
and mark where content is *transformed* (and by whom). To understand any slice, **render a perspective —
never read the whole graph**:
- `pnpm arch:viz cluster:<domain|terminal>` — a concern bundle + the carriers it touches
- `pnpm arch:viz flow:<id>` · `path:<carrier>` — a data path · a carrier's writers→readers
  (`--lod=0..3`, `--scale`; **deterministic** → `docs/views/*.svg`).

**It must not drift** (`gate:dataflow`, in `pnpm gates` → pre-push/CI): (a) every SoC container on disk has
a node — add a module without registering it and CI goes **red**; (b) one writer per carrier + a reader
never re-derives a producer's data (`render-only`); (c) the chart is **sealed** — any edit fails CI until
you review the diff and run `pnpm seal:arch` (the chart is the *asserted-true* baseline; drift from it is
deliberate-only). **So: when you add/move a module, change a data flow, or alter ownership/contract, update
`architecture.calm.json` in the SAME change and re-seal.** This is P23 — the data-flow dimension the rest of
the gate suite (code-coupling) did not model.

## The preflight — prove the WHOLE execution surface in planning (consolidated)

The validation economy (§8.2) is only as good as its **coverage**. A thin preflight that checks *resources*
but not the rest lets foreseeable failures cascade into execution — a full session was burned on exactly that
(Learning #35). **Before a plan is approved, RUN + RECORD a cheap preflight that PROVES (not assumes) every
line below; an unproven line is a blocker to clear first.** Each is a one-call instrument in planning:

- **Resources** — reachable · authenticates · **funded/in-balance** · runs-what's-needed — not merely
  recorded (Learning #28).
- **Operations + permissions** — every verb the run performs (build/rebuild · redeploy · restart · `down -v`
  · migrate · push · send-outward): proven you **CAN** (context/tooling/base image present, target reachable)
  **and MAY** (permitted · right lane · non-disruptive to others) (Learning #31).
- **Artifact currency** — the deployed image/code **is** the source under test; a stale artifact is a
  foreseeable failure, not a surprise (Learning #31).
- **Topology binding** — every actor (services **and the human's** browser → UI → backend) binds to the
  **same intended target stack**: no same-port localhost shadow, no silently-failed tunnel. Confirm with a
  **per-stack marker** the target returns + `lsof`/`docker ps`, before trusting any observation (Learning #34).
- **Identity** — one standard test identity end-to-end (the **logged-in identity == the spawning identity**);
  validated, not assumed (Learning #32).
- **Contract coverage** — every endpoint the real/**vendored client actually CALLS** is mounted — not just
  the ones that look canonical (Learnings #30, #33).

Together these ARE the failure model, surfaced **before** the scarce human run, not during it. The human is
the ground-truth oracle, not the probe that discovers a thin-preflight gap (loop §8.3, Learning #29).

## Planning embeds the rules — the plan is self-bounding (ADR-0021)

A plan does not merely *reference* the constitution; it **embeds the governing rules inline** so execution cannot
drift. It (a) states the **end-goal** as a falsifiable, gate-backed definition-of-done (ADR-0017); (b) carries a
**"Rules in force"** block with the *text* of every principle/loop-rule the work must obey; (c) walks each
objective as a **visible hop** — *Objective → Expected → Observation (facts + what was NOT checked) → Verdict
(expected→continue · unexpected→STOP+learn+re-plan) → end-goal check* — naming its principles/gates and an
explicit **"Unexpected if:"** trigger; and (d) operationalizes any "better than X" claim as a **specific green
gate** (P9), never prose. A plan without inline rules, per-hop `Expected`, or its pre-staged forks is not in the loop.

## How you work — the expectation–reality loop (§8)

1. **Expect first.** State what the system *should* do and what "done" looks like for the current
  objective *before* acting. You can't detect a divergence you never defined.
2. **Validate cheaply by instrument — but it's approximate.** Gates / tests / the `eval/`
  `replay`·`analyze`·`benchmark` path do the broad, cheap filtering — fast, reproducible, no human. But
   pass/fail is a *proxy*: it can mis-define success or mis-interpret the signal. Cheap, not definitive —
   green is necessary, never sufficient (P19).
3. **The human is the ground-truth oracle — scarce + fallible.** Only a human can finally tell if it
  *actually works* — use the human where cheap tests can't define/interpret success. Spend it **last and
   least**, give a **minimal, fully-instructed surface** (the exact `🧑` step), and **cross-validate both
   ways** — a green instrument is provisional; a human "it works" is checked against an instrument. Then
   **instrumentalise the human's verdict** (a golden / eval baseline) so the cheap test calibrates to it.
   **The human is NOT a retry mechanism.** When a human-gated attempt fails (a **human-red**), do not bounce
   it back ("try again") — that re-spends the scarce oracle to discover what an instrument should have caught.
   **Fully elaborate the failure first**: reproduce it WITHOUT the human, root-cause it completely, fix it, and
   **prove the fix green on a cheap instrument** (a dry spawn / API call / replay that re-creates the human's
   path). Only then request the next human delivery attempt — each one must be *earned* by a green instrument,
   not used as the probe. "Retry whenever you're ready" is a smell: it means the work was handed back
   un-de-risked (Learning #29).
4. **An unexpected error is a STOP.** Reality ≠ expectation ⇒ stop. No paper-over, no blind retry.
  **But UNEXPECTED ≠ unfinished work.** A scaffold you're meant to complete, a stub awaiting its impl, "not
   built yet" — that is *expected work*: **finish it autonomously, do not stop or escalate.** STOP is reserved for a
   genuine *contradiction* (a frozen contract field that can't be met, a false premise, a real conflict) — not for
   remaining scaffolding (Learning #21).
5. **Root-cause every surprise — earn the learning with the human, promote it twice.** Each surprise is a
  *symptom* of a missing/violated principle. Interpret it **with the human** (never mint a learning from
   an instrument alone), fix the instance, then promote it to **both** the **architecture** (a principle +
   gate + ADR) **and** the **learnings log** (`[docs/LEARNINGS.md](docs/LEARNINGS.md)` — always, even for a
   practice/candidate with no P-number). How the principle-set grows (P18→P21). See ADR-0014/0018.
6. **State the objective, then report facts — not evaluations.** A report assesses the result-state
  against the **current objective** (*result vs objective*), so name the objective first, then ship the
   **raw evidence** — the command + actual output, counts/names of what was checked, and **what was NOT**
   (fakes vs real, type-checked vs executed, instrument vs human). "Done"/"validated"/"works" is *your*
   interpretation — state it separately and labelled, downstream of the facts, so the human assesses
   result-vs-objective themselves (P21, ADR-0016/0017).

> *Expect → instrument → (human: minimal, cross-validated) → stop on surprise → root-cause to a principle → codify. Report facts, not evaluations.*

## Every hop is VISIBLE — the objective ledger (the forcing function)

The loop above silently degrades into "do → report" unless each hop is **written before acting**.
With no stated expectation there is nothing for reality to violate, so nothing ever registers as
*unexpected* — and the learning mechanism never fires. So **every** objective is stamped in this exact
shape, hop by hop, where the human can see it:

- **Objective:** the one current *go*.
- **Expected:** the concrete, **falsifiable** result predicted *before* acting — the specific
numbers / states / shapes that "done" will show, and how they'll be checked.
- *…act…*
- **Actual:** raw evidence — command + output, counts/names, and **what was NOT** checked.
- **Verdict:** **EXPECTED** → continue to the next planned objective (state the next one + its Expected) ·
**UNEXPECTED** → **STOP**, interpret *with the human*, learn (loop step 5), re-plan.

**No `Expected` written ⇒ you are not in the loop.** An objective that closes without an explicit
Actual-vs-Expected **Verdict** is incomplete. There is no automated check for this — **the human reading
the ledger IS the gate** (the visibility is the enforcement). Keep `Expected` short and testable; an
unfalsifiable expectation ("it should work") can't catch a surprise.

**During execution, every message leads with the current hop's DoD.** Restate the open objective and its
falsifiable `Expected` (the definition-of-done) at the top of each step, so the human never scrolls to know
what "done" means *right now* — the target rides with every message, it is not stated once and forgotten.

**A verdict ships its data points, never a bare "green."** "Done" / "failed" is *your* interpretation; back
it with the **actual observed values** — the counts, names, states, exit codes, the specific numbers that
made you read it that way — so the human re-derives the verdict from the data, not your word (P21). "Green"
with no data points under it is not a report; "9/9 gates, 16 node + 3 py pkgs" is.

## The hard rules (from the constitution)

- **Green or it didn't happen.** `pnpm gates` must pass; an artifact "exists" only when gate-green (P9).
- **Prove at the altitude of the claim (P19).** A user-facing behaviour needs **L4** evidence, not just
L1–L3 green. Name which level a "green" rests on.
- **Report state from evidence, not intent (P21).** Don't claim a success you haven't observed.
- **Contracts & principles ride `lane:contract`** — a human-reviewed change, recorded as an ADR under
`docs/adr/`. Everything else merges on green gates.
- **Fix at the point of INTRODUCTION, not the point of OBSERVATION — the brick that *owns* a symptom is the one that *introduces* it, never the one where it *surfaces*.** A defect is observed downstream (a consumer: terminal, relay, renderer) but introduced upstream (the producer that first violated the contract). **Trace it hop-by-hop from where you see it back to where it's born — and only then choose the fix site.** **Never patch a symptom in a consumer to compensate for a producer's defect**: that workaround scatters compensating logic across every consumer, leaves the bug live for all the *other* consumers (the same producer feeds them too), and rots the contract — *we never work around our own bugs.* *Smell:* "I'll make the terminal ignore / normalize / de-flicker the bad frames." "Where I see it" ≠ "where it's introduced." **Reproduce with no live meeting before you fix.**
- **A brick's front door is per-runtime; inject runtime dependencies.** When a brick spans runtimes
(browser + node), its `.` front door is **types-only** (fully erased ⇒ zero browser runtime); any node-only
capability (e.g. an fs-backed validator) lives behind a **separate subpath** (`pkg/validate`). A cross-brick
**runtime** dependency is **injected** (DI), never hard-wired, so the browser path defaults to a clean
pass-through and node-only code (`node:fs`) is never dragged into a browser bundle. *Smell:* a value
re-export of node-only code from a front door that something imports only for a type. The **bundler is the
L4 gate** for this boundary — a logic (tsx) test passes right over it.
- **The core owns its contracts; clients adapt (anti-corruption, dependencies point inward).** Each domain
emits its contract in the **clean canonical shape**; adapters and legacy / vendored clients absorb every
impedance mismatch **on their own side**. A consumer's legacy name, field shape, or quirk must **never** be
pushed upstream into the core — translate at the client boundary, never bend the core to a client. *Smell:* a
core publisher emitting a frame/field named for, or mapped to, a specific client's vocabulary (e.g. the core
remapping its own enum value to a dashboard's spelling). When you catch the core carrying a client's shape,
the fix moves the translation **out to the client**, not deeper into the core.
- **Source states the designed present, not its history.** Code comments, file headers, and docs describe
what the thing **is** and **why**, in terms of *today's* contracts — never what broke, what was stale, what
the fix was, or which Learning/ADR number prompted it. The **surprise → root-cause → fix narrative lives ONLY
in `docs/LEARNINGS.md`.** A comment like "before this it was X", "this was the Y bug", "the carve had…",
"(Learning #N)" is **rot** — it freezes transient context into the source and decays into a lie. Write the
comment as if the code had always been this way. (This is the *output* side of P21: report from designed
state, not from the incident.)

## Closing the loop — a learning becomes a principle, minted into the plan (ADR-0021)

A learning is not "done" when it lands in `docs/LEARNINGS.md`. The ledger holds the **incident** (the
surprise/root-cause); the durable rule must be **conceptualized as a principle** and promoted to the
**architecture** (this file / `docs/ARCHITECTURE.md` via `lane:contract`), so that **planning embeds it
inline** (ADR-0021) and the next plan cannot repeat the class of failure. Three homes, three jobs, no overlap:
**ledger = the incident · architecture = the durable principle · source = only the design.** Promoting a
learning into a code comment is the anti-pattern — that is the ledger leaking into the source. Close the loop:
incident → principle → plan, not incident → comment.

