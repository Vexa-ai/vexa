# Stage: plan

| field        | value                                                                            |
|--------------|----------------------------------------------------------------------------------|
| Actor        | AI + human                                                                       |
| Objective    | Produce `scope.yaml` + DoD/Registry change proposals; get human sign-off on both.|
| Inputs       | `releases/<id>/groom.md` (approved packs)                                        |
| Outputs      | `releases/<id>/scope.yaml` + `releases/<id>/plan-approval.yaml` (human-signed)   |

## Steps
1. `lib/stage.py assert-is plan` — halt if wrong stage.
2. For each approved pack, draft an issue: `id`, `problem`, `hypothesis`, `proves[]` (bindings into `registry.yaml`), `required_modes`, `human_verify[]`.
3. If the proposal needs NEW checks (IDs not in `registry.yaml`), list them under `registry_changes_approved` in `plan-approval.yaml`.
4. If touched features' DoDs change (new, reweighted, removed), list each change in `dod_changes_approved`.
5. Write `scope.yaml` + `plan-approval.yaml` (with `approved: false` on every item).
6. HALT. Present to human. Human reviews every item line-by-line, flips `approved: true` OR sends back for revision.

## Exit (mechanical gate enforced by stage.enter(develop)):
- `scope.yaml` parses; every issue has non-empty `hypothesis` and ≥1 `proves[]` binding.
- Every `{check: X}` in `proves[]` either exists in `registry.yaml` OR appears under `registry_changes_approved` in `plan-approval.yaml`.
- `plan-approval.yaml` has `approved: true` on every listed item (objectives, DoD changes, registry changes).

## May NOT
- Edit code (that's `develop`).
- Run tests (that's `validate`).
- Touch infra (that's `provision` / `deploy`).
- Auto-advance without human sign-off.

## Next
`develop` — once `plan-approval.yaml` is fully signed.

## AI operating context
You are in `plan`. Your objective is to produce a complete, human-approvable `scope.yaml` + `plan-approval.yaml`. Approval is line-by-line; do NOT mark items `approved: true` yourself. Do NOT edit code, run tests, or skip any approval line. If asked to implement anything: "I am in plan; I may not code. After approval we'll advance to develop."
