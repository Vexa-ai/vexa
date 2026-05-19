# Stage: human

| field        | value                                                                           |
|--------------|---------------------------------------------------------------------------------|
| Actor        | AI (prepare) + human (sign off)                                                 |
| Objective    | (A) code review + (B) bounded manual eyeroll; both required before ship.       |
| Inputs       | `scope.yaml` + `human-always.yaml` + auto-generated `code-review.md`            |
| Outputs      | `releases/<id>/human-approval.yaml` — both parts `approved: true`               |

## Part A — Code review

AI generates `releases/<id>/code-review.md` with:
- Per-commit summary (what + why + risk + touched DoDs).
- Diffs grouped by concern, not git order.
- Risk notes (invariants, ordering deps, anything a reviewer might miss).
- Open questions for the human.

Human reads + approves → Part B unlocks.

## Part B — Bounded manual eyeroll

AI generates `releases/<id>/human-checklist.md` (union of `human-always.yaml` accumulated items + scope-specific `human_verify[]` + URLs/env/assets). Human ticks each box.

If human finds a bug:
- Applies every release → graduate to `human-always.yaml` (Registry grows).
- Specific to this release → block ship, file as scope issue → back to `triage`.

## Steps
1. `lib/stage.py assert-is human`.
2. Generate `code-review.md` (AI) → human reviews Part A → sets `code_review_approved: true`.
3. Generate `human-checklist.md` (mechanical) → human ticks every item → sets `eyeroll_approved: true`.
4. Write `human-approval.yaml` with both parts + signer + timestamp.

## Exit
Both parts signed. `human-approval.yaml` contains:
```yaml
code_review_approved: true
eyeroll_approved: true
```

## May NOT
- Edit code.
- Change infra.
- Skip code review.
- Auto-sign either part.

## Next
`ship` — on both parts signed.
`triage` — if human found a gap (either part).

## AI operating context
You are in `human`. Two parts: (A) prepare the code review packet, (B) translate any human bug-report into a formal issue via `release-issue-add` (AI does this, not the human — see §7-human skill). Required fields (`GAP`, `NEW_CHECKS`) are derived by you, not asked from the human. You may NOT edit code or change infra. If a human report becomes a new scope issue, transition to `triage` via `stage.py enter triage`.
