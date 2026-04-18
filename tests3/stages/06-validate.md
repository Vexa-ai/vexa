# Stage: validate

| field        | value                                                                   |
|--------------|-------------------------------------------------------------------------|
| Actor        | mechanical                                                              |
| Objective    | Three-phase validate (plan / execute / resolve); emit Gate verdict.     |
| Inputs       | `scope.yaml` + `features/*/dods.yaml` + `registry.yaml`                 |
| Outputs      | `.state/reports/<mode>/*.json` + `reports/release-<tag>.md` + AUTO-DOD  |

## Steps (Makefile: `release-validate`)
1. `lib/stage.py assert-is deploy`.
2. **PLAN** — `lib/run` builds the execution graph: filter `registry.yaml` by scope × modes, group by `state:`, order stateful serial, stateless parallel.
3. **EXECUTE** — run the graph; each entry emits `.state/reports/<mode>/<test>.json` via `test_begin/step_*/test_end` helpers (`lib/common.sh`).
4. **RESOLVE** — `lib/aggregate.py` loads sidecar DoDs + reports, evaluates every DoD, computes per-feature confidence, writes `reports/release-<tag>.md` + updates feature README AUTO-DOD blocks.
5. Gate verdict:
   - **green** → `lib/stage.py enter human`.
   - **red**   → `lib/stage.py enter triage`.

## Exit
Gate verdict recorded; stage transitioned to `human` (green) or `triage` (red).

## May NOT
- Edit code.
- Change infra.
- Re-try failed tests without root-cause investigation ("flake retry" is forbidden — see triage).

## Next
`human` (on green) | `triage` (on red).
