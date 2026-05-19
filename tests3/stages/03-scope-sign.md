# Stage: scope-sign

**Level:** scope · **Role:** sign · **Inner-loop:** `design → deliver → verify → sign`

| field        | value                                                                       |
|--------------|-----------------------------------------------------------------------------|
| Actor        | human (AI prepares the artefact)                                            |
| Objective    | Approve `scope.yaml` (post-audit) line by line.                             |
| Inputs       | `scope-design.md` + `scope.md` + `scope.yaml` + `scope-verify.md`             |
| Outputs      | `releases/<id>/scope-approval.md`, using `tests3/templates/scope/scope-approval.md`; optional generated `scope-approval.yaml`; plus the `scope-sign` sign block inside `scope.md`. |

## Steps
1. `lib/stage.py assert-is scope-sign`.
2. AI generates `scope-approval.md` mirroring every `scope.yaml` item with
   unchecked Markdown boxes.
3. Human reads scope + audit findings; checks boxes and writes the summary in
   their own words.
4. If tooling needs YAML, generate `scope-approval.yaml` from
   `scope-approval.md`; do not ask the human to edit YAML.
5. If any line bounces → reverse-edge back to `scope-deliver`.

## Exit

The single sign carried by `releases/<id>/scope.md` at the `scope-sign`
entry in its `signs:` list must be valid per `tests3/sign-template.md`
(rules 1-9). Specifically:

- `signs[stage=scope-sign].attestation_confirmed` has all four flags
  `true` (`read_multiple_times`, `understands_what_and_why`,
  `confirms_balance_deliverable`, `i_authored_this_doc`).
- `signer`, `signed_at`, `signed_artefact.git_sha` filled.

`scope.md` is the propagating release doc. `scope-sign` is the FIRST of
its four control-point signs (develop-sign, stage-sign, release add
the others). AI MUST refuse to transition forward if this sign fails.

## May NOT
- AI flips `approved: true` (human signal only — see `vexa/CLAUDE.md` "You are NOT the user").
- Ask the human to sign YAML.
- Edit scope.yaml in-place (bounces are explicit reverse-edge transitions).

## Next
`develop-design` — on full approval.
`scope-deliver` — on rejection.
