# Claude Code — start here

This repo operates under a **strict stage state machine**. Before doing
*anything*, you must orient yourself.

## First action — ALWAYS, every session

```bash
python3 tests3/lib/stage.py probe
```

This prints the current stage + legal next stages + a one-line objective.

## Obey the stage

Each stage has an explicit contract at `tests3/stages/NN-<name>.md`
(objective, inputs, outputs, exit condition, **may NOT** list). Read it
before taking any action. If the user asks for something outside the
current stage's `may NOT` list, **refuse** with a stage-aware message:

> *"Currently in `<stage>`; that action is forbidden (`<rule>`). To do it,
> transition via `<legal next stage>`."*

## Common situations — map to stage

| user says                                    | likely stage                     |
|----------------------------------------------|----------------------------------|
| "debug X / fix Y / write code"               | `develop` (must be entered from `plan` or `triage`) |
| "run the tests / check the gate"             | `validate` (must be entered from `deploy`) |
| "classify failures / what broke"             | `triage` (entered from `validate` on red) |
| "validate checklist / sign off"              | `human` (entered from `validate` on green) |
| "ship it / merge to main"                    | `ship` (entered from `human`) |
| "start a new release / groom issues"         | `groom` (entered from `idle`) |

If the current stage doesn't match what the user asked for, **don't
bumble around trying to make it work**. State the mismatch and the
legal transition path.

## Why this matters (read `tests3/README.md` for the full model)

Vexa uses one nested-loop release protocol:

- **INNER** — validate → triage → develop → deploy → validate. Mechanical,
  cheap, repeats many times per day.
- **MIDDLE** — validate (green) → human (code review + eyeroll) → ship.
  Bounded human attention only.
- **OUTER** — ship → market → issues → groom. Slow, expensive, real users.

Drifting between stages destroys the Registry's regression guarantee
and the MIDDLE loop's boundedness. Your stage-awareness is the
enforcement.

## You are NOT the user

You may not mark `plan-approval.yaml`, `human-approval.yaml`, or any
stage's exit condition `approved: true` without the user explicitly
saying so in the current turn. Approval is a human signal — your job is
to prepare the material for it, not to grant it.

## If you're lost

`python3 tests3/lib/stage.py probe` again. Then read
`tests3/stages/<current>.md`. Then read `tests3/README.md`.
