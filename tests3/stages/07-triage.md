# Stage: triage

| field        | value                                                                        |
|--------------|------------------------------------------------------------------------------|
| Actor        | AI + human (AI classifies; human decides)                                    |
| Objective    | Classify every failing DoD as regression OR gap; surface next-fix target.   |
| Inputs       | `.state/reports/<mode>/*.json` + `reports/release-<tag>.md` + failing DoDs   |
| Outputs      | `releases/<id>/triage-log.md` — classification + next-fix target             |

## Steps
1. `lib/stage.py assert-is triage` — halt if wrong stage.
2. Parse release report; enumerate every DoD with status ≠ `pass`.
3. For each failing DoD, classify:
   - **regression** — existing code path broken; cite the bound check, expected vs actual, touched commits.
   - **gap** — the test isn't reliable; cite root cause (race, timing, infra fragility, misowned DoD). Do NOT call it "flake".
4. Write `releases/<id>/triage-log.md` with one entry per failing DoD.
5. HALT. Present to human. Human writes `fix this first: <DoD-id>` or `accept this gap, do not fix` in the log.

## Exit
`triage-log.md` contains a human-written line: `fix this first: <DoD-id>` or `accept this gap, do not fix`.

## May NOT
- Edit code (that's `develop`).
- Run tests, rebuild images, re-provision.
- Classify any failure as "flake" without root-cause analysis.
- Advance stage without human confirmation.

## Next
`develop` — on human designates next-fix target (usual path).
`human` — if all failures are accepted gaps (rare).

## AI operating context
You are in `triage`. Your objective: classify every failing DoD as regression or gap and surface the next-fix target for human decision. Read release report + `.state/reports/*.json` + sidecar DoDs + `registry.yaml`. Produce `triage-log.md` — one entry per failing DoD. Exit when the log exists and the human has designated the next fix. You may NOT edit code, run tests, or advance stage. Refuse ad-hoc fixes: "I am in triage; I may not edit code. That requires stage develop." If a failure looks non-deterministic, investigate root cause. Do not classify as "flake".
