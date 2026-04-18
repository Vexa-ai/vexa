# Stage: develop

| field        | value                                                                          |
|--------------|--------------------------------------------------------------------------------|
| Actor        | human (code) + AI (assist)                                                     |
| Objective    | Write code + tests + `dods.yaml` entries implementing the approved scope.      |
| Inputs       | `scope.yaml` + (if entered from `triage`) `triage-log.md`                      |
| Outputs      | Commits on `dev` branch                                                        |

## Steps
1. `lib/stage.py assert-is develop` — halt if wrong stage.
2. For each scope issue, implement code + test + (if needed) add / update DoD in feature sidecar.
3. Every new check ID referenced by scope `proves[]` must exist in `registry.yaml` before moving on.
4. Commit. Trailer format: `release: <id> · stage: develop`.

## Exit
All scope issues have commits; every new `proves[]` check id exists in `registry.yaml`.

## May NOT
- Touch infra (`provision` / `deploy`).
- Run validate.
- Advance stage without all scope commits present.

## Next
`provision` — if entered from `plan` (first-time infra).
`deploy` — if entered from `triage` (infra already up; just push the fix).

## AI operating context
You are in `develop`. You help the human write code + tests + DoDs per the scope. You may edit files under `services/`, `features/`, `tests3/tests/`, `tests3/registry.yaml`. You may NOT run validate, touch infra, or advance stage yourself. Every code change aligns with a scope issue or the triage-log; refuse ad-hoc work: "I am in develop; show me which scope issue / triage item this serves."
