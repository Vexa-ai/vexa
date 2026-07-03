# ADR 0019 — One git worktree per chat; integrate via PR

**Status:** accepted · 2026-06-20 · enforces §8 "small PR on trunk"

## Context

Two sessions sharing **one working tree** collided: a `meetings/services/meeting-api` package appeared
mid-session from *another* chat (a surprise this session nearly overwrote), and earlier a write-capable
audit agent **destroyed the uncommitted `bot/` brick** on the shared dirty tree (Learning #7). Same class:
concurrent chats on one working tree don't isolate uncommitted state, and shared files (the constitution,
`scripts/gates.mjs`, the lockfile) race.

The audit also found the deeper enabler — **no branches, no PRs, everything direct on one local `main`**.
So isolation is not a new idea: it is the constitution's own trunk-based + PR model (§8), never enforced
because there was a single shared tree.

## Decision

**Each chat works in its own `git worktree` on a short-lived branch; never two chats on one working tree.**

- A chat creates its worktree from the repo, e.g.:
  `git worktree add ../v0.12-<chat-slug> -b chat/<slug>` — its own checkout + index (uncommitted state is
  fully isolated), sharing the one `.git` object store.
- Work is committed on that branch and integrated to the integration branch via a **PR** (small diff,
  gates required to merge; `lane:contract` PRs are human-gated). `contracts/*.v1` and principle changes
  ride a reviewed PR as always.
- When the work lands, the worktree is removed (`git worktree remove`). The harness's per-agent
  `isolation: worktree` is the same mechanism for spawned agents.
- **You never touch another chat's worktree or its uncommitted files.** Surprise files from another chat
  on a shared tree are that chat's to own — surface them (the expectation–reality loop), don't adopt them.

## Consequences

- The "appeared from another session" surprise and the lost-brick class are structurally prevented:
  uncommitted state lives only in the owning chat's worktree.
- Integration is explicit (PR + gates) instead of implicit (everyone mutating one tree) — the model the
  constitution always specified.
- Cost: a worktree per chat + merge discipline. Cheap against a lost brick or a silent overwrite.
- Transitional note: work already on the single shared tree is committed by its owner; this rule governs
  going forward.
