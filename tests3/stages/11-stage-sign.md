# Stage: stage-sign

**Level:** stage · **Role:** human · **Inner-loop:** `do → audit → human → next`

| field        | value                                                                       |
|--------------|-----------------------------------------------------------------------------|
| Actor        | human (AI prepares the artefacts)                                           |
| Objective    | Final code review + canonical-stack eyeroll before release, using the canonical human-validation harness with the human handed exact deployment target URLs and the machine verifying each fresh live artifact. |
| Inputs       | `scope.yaml:human_checklist_draft` + `human-always.yaml` + canonical stack from `stage-deliver` + validate-report green |
| Outputs      | `releases/<id>/code-review.md` + `releases/<id>/human-checklist.yaml` + `releases/<id>/human-approval.yaml` + a human handoff that names the canonical deployment target URLs to validate (dashboard, gateway, docs, helm/LKE entrypoints) — `code-review.md` and `human-checklist.yaml` MUST each open with the CTO briefing block per `tests3/communication-standard.md` |

## Two parts

### Part A — Code review

AI generates `code-review.md`: per-commit summary (what + why + risk + touched DoDs), diffs grouped by concern, risk notes, open questions. Human reads → flips `code_review_approved: true`.

### Part B — Canonical-stack eyeroll

AI generates `human-checklist.yaml`: union of `human-always.yaml` + `scope.yaml:human_checklist_draft` items that require canonical infra (helm-mode, multi-mode, prod-equivalence). Human walks the throwaway canonical stack item by item, flips `approved: true` per line, then sets `eyeroll_approved: true`.

Live bot/action checks MUST follow [human-validation-harness.md](../human-validation-harness.md): machine dispatches the action, human admits/listens/judges, machine verifies the exact stage artifact and report path. Older local/develop evidence can be supporting context only.

## Steps
1. `lib/stage.py assert-is stage-sign`.
2. Generate `code-review.md` → human approves Part A.
3. Hand off the exact canonical deployment target URLs the human will validate. "Stack green" is insufficient; the delivery must tell the human where to go.
4. Generate `human-checklist.yaml` → for live items, run the canonical harness against the throwaway stack and exact fresh artifact → human walks throwaway stack at those URLs → approves Part B.
5. Write `human-approval.yaml` with both parts + signer + timestamp.

## Exit

- `code-review.md` exists and is referenced from the sign attestation.
- Human received concrete canonical deployment target URLs and could begin validation immediately without discovering ingress/node-port/host details manually.
- Any live human-gate artifact is named with exact stage stack, meeting id/native id, bot name, image tag, commit, and registry report.
- `human-checklist.yaml` carries checklist items each `approved: true`.
- The `stage-sign` entry in `releases/<id>/scope.md`'s `signs:` list
  is valid per `tests3/sign-template.md` with all five `stage-sign`
  attestation flags `true` (`canonical_validate_matrix_green`,
  `code_review_approved`, `canonical_stack_eyeroll_approved`,
  `doc_still_describes_reality`, `i_authored_any_new_prose`).
- `signed_artefact.git_sha` matches current HEAD.
- If `scope.md` was revised since the `develop-sign` sign,
  `notes_since_prior_sign:` is non-empty.

This is the THIRD of four control-point signs on `scope.md`. AI MUST
refuse to transition forward (to `release-deliver`) if the sign fails validation.

## May NOT
- AI flips either part `true`.
- Skip Part A or Part B.
- Edit code (any bug bounces to `develop-deliver`).
- Reuse develop-sign meeting evidence as stage-sign proof.

## Next
`release-deliver` — on both parts green.
`develop-deliver` — on rejection.
