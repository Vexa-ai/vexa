# ⚠ STALE 2026-05-12 — descriptions reference the OLD state machine (idle→groom→plan→develop→provision→deploy→validate→triage→human→ship→teardown). The current model has 13 stages with do→audit→human pattern at three levels. Authoritative: tests3/stages/release-flow.md + per-stage 00-idle.md…12-teardown.md. Full skill-directory rewrite is v0.10.7 pack state-machine-docs-rewrite.

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
4. Write `releases/<id>/groom.md` — one section per pack.
5. HALT. Human picks which packs advance to `plan`.

## May NOT

- Write `scope.yaml` (that's `plan`).
- Edit code or touch infra.
- Invent packs to fill quota.

## Next

`plan` — once human approves at least one pack.
