# Stage: develop-sign

**Level:** dev · **Role:** human · **Inner-loop:** `do → audit → human → next`

| field        | value                                                                          |
|--------------|--------------------------------------------------------------------------------|
| Actor        | human (AI prepares the artefact)                                               |
| Objective    | Eyeroll local validation through the canonical human-validation harness: deliver ready-to-validate LOCAL target URLs, machine-dispatch any real bot/action under test, have the human admit/listen/judge, then verify the exact fresh artifact before `local-human-checklist.yaml` can be signed. |
| Inputs       | `scope.yaml:human_checklist_draft` + LOCAL=1 lite + compose stack running      |
| Outputs      | `releases/<id>/local-human-checklist.yaml` (all `required_for_develop_human` items `approved: true`; `sampled_or_risk_accepted` items either walked or explicitly risk-noted) + a human handoff that names the LOCAL target URLs to validate (`lite` / `compose` dashboard, gateway, docs) — paired with `local-human-brief.md` containing the CTO briefing block per `tests3/communication-standard.md` |

## Steps
1. `lib/stage.py assert-is develop-sign`.
2. AI materialises `local-human-checklist.yaml` from `scope.yaml:human_checklist_draft` — only items walkable on the LOCAL=1 lite + compose stack (helm-mode items defer to `stage-sign`).
3. AI hands off the concrete LOCAL validation targets as URLs, not prose like "the stack is up". Minimum expectation: dashboard URL(s) plus the relevant docs / API roots the checklist uses.
4. AI includes a concise delivery assurance block before the walkthrough:
   `Expected from human`, `Not expected from human`, and `Already assured by registry/harness`.
5. For any real-meeting/product-path item, AI uses `tests3/human-validation-harness.md`: machine dispatches the bot/action, human admits/listens/judges, machine verifies the exact fresh artifact and registry report.
6. Human walks every `required_for_develop_human` item at those URLs, flips `approved: true` per required line only after the machine verdict and human sensory judgment agree. `sampled_or_risk_accepted` items are not required for the develop-human exit, but each unwalked item must remain `approved: false` with a note that names the risk accepted or the later stage where it will be sampled.
7. If any item fails eyeroll or a fresh harness artifact fails a registry check → reverse-edge to `develop-deliver`.

## Prerequisites (AI refuses entry if any unmet)

- **`tests3/tests/static/registry-canonical.sh` green** — `registry.yaml` is the canonical source of truth for check IDs across the current release's `scope.yaml` and the `tests3/tests/**/*.sh` prove scripts. Drift between them means the matrix returns "missing" on a real-running prove or accepts a phantom id with no implementation; the human gate would open on a colour-blind matrix. Three steps must all be ✅:
  - `REGISTRY_COVERS_ALL_SCOPE_PROVES` — every `{check: X}` in scope.yaml `proves[]` is registered.
  - `REGISTRY_COVERS_ALL_SCRIPT_STEP_IDS` — every `step_pass/step_fail <ID>` emitted by prove scripts is registered.
  - `REGISTRY_HAS_NO_ORPHAN_IDS` — every registry entry has at least one referrer (warn-pass; cleanup pack tracked separately).
- **`walkability-smoke.sh` green on both `compose` AND `lite` modes** — confirms the stack is actually walkable (auth round-trips, meetings data present, dashboard renders, TTS round-trips, transcription URL non-placeholder). AI refuses transition into `develop-sign` if either red. See `tests3/tests/walkability-smoke.sh`.
- **`scope-proof-gate-local.sh` green** — every scope-bound prove that is runnable on LOCAL=1 (`lite` / `compose`) is present and `pass` in the current LOCAL reports. This is stricter than registry-canonical: it proves the required cells are not merely named, but actually executed and green before the human checkpoint opens.
- **`tests3/human-validation-harness.md` followed for live human checks** — old passing reports are not enough for a fresh human gate. If the human admits a new bot, the machine must verify that exact `meeting_id` (for example with `LIVE_BOT_MEETING_ID=<id> bash tests3/tests/live-bot-transcript-pipeline.sh`) before presenting pass.

## Exit
- Human received concrete LOCAL deployment target URLs and could open the validation surfaces immediately without discovering ports/hosts by hand.
- Human received a concise assurance of what is expected from them, what is not expected because registry/harness already tested it, and which registry/harness facts are green.
- Any real-meeting validation cites the exact fresh artifact (`meeting_id`, native id, bot name, report path) and a pass/bounce verdict from the canonical harness.
- `local-human-checklist.yaml` carries every `required_for_develop_human` item as `approved: true`; every `sampled_or_risk_accepted` item is either `approved: true` from a real walk or left `approved: false` with an explicit sampled/risk-accepted note.
- The `develop-sign` entry in `releases/<id>/scope.md`'s `signs:` list
  is valid per `tests3/sign-template.md` with all four `develop-sign`
  attestation flags `true` (`doc_still_describes_reality`,
  `local_validation_green`, `local_human_checklist_walked`,
  `i_authored_any_new_prose`). `signed_artefact.git_sha` matches current HEAD.
- If `scope.md` was revised since the `scope-sign` sign, `notes_since_prior_sign:`
  is non-empty.

This is the SECOND of four control-point signs on `scope.md`. AI MUST
refuse to transition forward if the sign fails validation.

## May NOT
- AI flips `approved: true`.
- Skip required items by claiming "covered by automated proves" — the eyeroll is the point.
- Treat an unwalked sampled/risk-accepted item as approved. It stays false until a human actually walks it.
- Present old meeting evidence as proof of a new human-walk meeting. Historical evidence can explain readiness; it cannot sign a fresh harness artifact.

## Next
`stage-deliver` — on full approval.
`develop-deliver` — on rejection.
