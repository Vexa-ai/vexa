# Plan template — execution-tracked, role-split (dev ⊥ followup)

Copy this skeleton when starting a plan (plans live in `~/.claude/plans/<slug>.md`). It exists so a plan is
a **living source of truth**: it tracks its own execution state and carries a shared channel between the two
roles that operate on it. Delete the parenthetical guidance as you fill each section.

---

## Two roles on one plan

A plan is worked by **two kinds of session**. The split keeps *higher-level reasoning open and detached from
implementation* — the steerer and the implementer are never the same hands, so the plan stays an honest,
independently-audited record rather than a self-graded one.

- **`dev` — the implementer (a normal session).** Owns a worktree. Reads and **writes code**, runs gates,
  builds/deploys. **Owns the Execution State tracker** (updates status + evidence as phases land) and
  **acknowledges handoffs** (writes the `ACK` lines). The only role that mutates the repo.

- **`followup` — the steerer (read-anything, plan-write-only).** May **read anything** — code, tests, the
  tree, git history, run *read-only* commands — to verify reality against the plan. **Writes ONLY the plan
  file.** Must **not** modify code, tests, configs, or run any mutating/stateful command (no edits, no
  commits, no builds, no deploys). Its product is: the **Execution State** sync (plan-vs-reality audit),
  **Decisions/Phases** revisions, and **Handoff** entries. It proposes; `dev` disposes.

**How the two stay in sync — the Handoff log (bottom of every plan).** When `followup` changes the plan it
drops a **handoff** entry; `dev` replies inline under **ACK** (`pending` → `ack` / `disputed`). The plan
file itself is the channel — both roles edit it concurrently, newest entry on top. A handoff names exactly
what changed and what decision (if any) `dev` must make next.

> Declare your role at the top of a session if it isn't obvious. If you are `followup`, treat every code
> path as read-only — your edits land in the plan, never in the repo.

---

# <Title> — <one-line intent>

## Execution State  *(last synced: <date> — by <role>, verified against working tree)*

(The plan-vs-reality tracker. `dev` updates as phases land; `followup` re-audits and corrects. Evidence is
**raw** — commands, counts, file:line — never "looks done".)

Legend: ✅ done · 🟡 partial / groundwork · ⬜ not started · 🧩 stub-as-planned

| Phase | Status | Evidence |
|---|---|---|
| 0 <name> | ⬜ not started | <file:line / command output / test count> |
| 1 <name> | ⬜ not started | … |

**Commit state:** <committed per-phase? all uncommitted? renames staged / files untracked?>

**Open deviations from plan (carry forward):**
- D1 — <where reality diverges from the plan, and which later phase it affects>

## Context

(Why this work exists — the concrete problem in the current code, with file references. The "before".)

## Approach

(The strategy in one paragraph — the shape of the path, not the steps.)

## Decisions (locked with the user)

(Each settled choice that bounds execution. A locked decision is not re-litigated mid-flight — if it must
change, that is a Handoff + re-plan, not a silent edit.)

- **<decision>:** <what + why>

---

## Phase N — <name>  *(pure refactor | behavior-changing | published-contract → deploy)*
(One coherent, independently-shippable slice. Steps, then the gate that proves it.)
- <step>
- **Gate:** <the falsifiable green that closes this phase — specific tests/commands, not prose (P9)>

## Phase classification (go/no-go)
- **Pure refactor / scaffold (gate = full test suite green):** <phases>
- **Behavior-changing (gate = updated tests + targeted validation):** <phases>
- **Published-contract / needs deploy:** <phases>

## Cross-cutting test strategy
- After **every** phase: the full suite green — each phase independently shippable.
- <guards that must hold across phases — import-graph, entrypoint boot, smoke>

## Critical files
- `<path>` — <what changes, in which phase>

## End-to-end verification
1. <the final, falsifiable proof the goal is met — the L4 evidence>

---

## Handoff log  *(shared channel: followup ⇄ dev)*

Protocol: `followup` drops a **handoff** entry after editing the plan; `dev` replies inline under **ACK**
(status `pending` → `ack` / `disputed`). Newest on top. Each handoff names what changed + any decision `dev`
must make.

### H1 — <date> · <from-role> → <to-role> · status: **pending ACK**
- <what changed in the plan and the verified facts behind it>
- <the decision(s) the other role must make>

> **ACK (dev):** _<reply here: status + decisions>_
