---
name: 1-plan
description: "Invoke after 0-groom — produce scope.yaml + DoD/Registry change proposals + get human line-by-line approval. Stage 02. Refuses to advance without plan-approval.yaml fully signed. Use when the user says 'plan the release', 'scope this', 'write scope.yaml', 'approve the scope'."
---

## Stage 02 — plan

See `tests3/stages/02-plan.md` for the full stage contract.

## First action — ALWAYS

```bash
python3 tests3/lib/stage.py assert-is plan
```

Legal predecessor: `groom` (and `groom.md` must have at least one approved pack).

## Steps

1. For each approved pack: draft an issue with `id`, `problem`, `hypothesis`, `proves[]` (bindings into `tests3/registry.yaml`), `required_modes`, `human_verify[]`.
2. If `proves[]` references check IDs not yet in `registry.yaml`, list them under `registry_changes_approved` in `plan-approval.yaml`.
3. If touched features' DoDs change (new / reweighted / removed), list each change in `dod_changes_approved`.
4. Write `releases/<id>/scope.yaml` + `releases/<id>/plan-approval.yaml` (with `approved: false` on every item).
5. HALT. Human reviews every item line-by-line, flips `approved: true` OR sends back for revision.

## Exit (mechanical gate enforced by `stage.enter(develop)`)

- `scope.yaml` parses; every issue has non-empty `hypothesis` + ≥1 `proves[]`.
- Every `{check: X}` in `proves[]` exists in `registry.yaml` OR appears under `registry_changes_approved` in `plan-approval.yaml`.
- `plan-approval.yaml` has `approved: true` on every listed item.

## May NOT

- Edit code (that's `develop`).
- Run tests or touch infra.
- Auto-advance without human sign-off.
- Mark approval items `approved: true` yourself.

## Next

`develop`.
