---
name: 0-groom
description: "Invoke at the START of a release cycle — cluster GitHub issues + Discord reports + internal notes into candidate issue packs. Stage 01 (OUTER→INNER ingress). Does NOT write scope.yaml (stage 02 plan does). Use when the user says 'groom', 'start a new release', 'what should land next', 'triage the backlog'."
---

## Stage 01 — groom

See `tests3/stages/01-groom.md` for the full stage contract.

## First action — ALWAYS

```bash
python3 tests3/lib/stage.py assert-is groom
```

If wrong stage, halt. Legal predecessor of `groom` is `idle`.

## Steps

1. Fetch open GitHub issues + recent Discord reports + internal notes.
2. Cluster by theme (bot lifecycle, webhooks, DB, transcription, …).
3. Draft one issue pack per cluster: symptom, owner feature(s), estimated scope, repro confidence.
4. Write `releases/<id>/groom.md` with **two layers, product first**:
   - **(A) Product framing at the top** — elevator pitch, "What we
     deliver" (3-5 user-visible changes + WHY), "Who wins" (personas
     × deltas), "Who sees no change". This narrative is the canonical
     source for scope.yaml summary, PR descriptions, CHANGELOG, and
     release notes. Never skip it.
   - **(B) Technical pack detail beneath** — signal sources, per-pack
     sections, suggested cycle shapes, approvals block.
5. HALT. Human picks which packs advance to `plan`.

## May NOT

- Write `scope.yaml` (that's `plan`).
- Edit code or touch infra.
- Invent packs to fill quota.

## Next

`plan` — once human approves at least one pack.
