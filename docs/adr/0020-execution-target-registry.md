# ADR 0020 — The execution-target registry; planning resolves where-work-runs in advance

**Status:** proposed · 2026-06-21 · `lane:contract` · enforces §8 "planning mode" + P14 · promotes Learning #22

## Context

Planning had no notion of **where work can run** or **what external resources exist**. So a plan would reach
**execution** and only then discover a wall: "the bot image is amd64, this Mac is arm64" was escalated as a
*block needing a user decision* when the project already had a designated amd64 host (`bbb`) for exactly that
(Learning #22 — *the deployment SSOT is `main`, the venue is `bbb`; consult them before escalating a "block"*).
That surface is **host/user-specific** (which machines, which credentials), so it cannot live in committed code;
and it is **config**, which P14 says is a validated contract delivered by env, with secrets as a class —
referenced, never inlined.

## Decision

**A repo carries a gitignored, host/user-specific execution-target & resource registry, and planning resolves
every stage's target + resources against it before execution.**

- The registry is a contract: `deploy/contracts/execution-targets.v1` (schema + goldens + `validate.mjs`). It
  lists **targets** (name · arch · caps: docker/compose/amd64-bot/gpu/…) and **resources** (services,
  credential-sets, storage, a meeting, a human gate). **Secrets are references only** (`vexa-secrets:`/`env:`),
  never values (P14) — enforced by the schema.
- **Committed template** `deploy/execution-targets.example.json`; **gitignored real** file
  `deploy/execution-targets.json` (the user copies the template, references secrets from `~/dev/vexa-secrets`).
- **In planning mode, before a plan is approved, every objective's `Runs on:` + `Resources:` is resolved
  against the registry, and any missing/unavailable target or resource is surfaced as a blocker to clear
  first** — never enter execution on an unresolved one. Every objective in the ledger carries `Runs on:` +
  `Resources:`.
- Enforced by **`gate:execution-env`** (the registry conforms; the template is always validated; the real file
  is validated when present) plus the planning preflight. `AGENTS.md` gains §"Where work runs".

## Consequences

- The "manufactured block" class is structurally prevented: a plan states where each stage runs and what it
  needs, checked up front, so execution never hits a "can't run here / no credentials" surprise.
- Cost: a registry to seed per host (cheap; one file). It is gitignored, so CI sees only the template — the
  green-or-skip gates already model "this target can't run docker here."
- This is the planning-mode counterpart to the expectation–reality loop: clearing blockers *is* stating the
  expectation about the execution environment before acting.
