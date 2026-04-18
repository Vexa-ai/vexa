---
name: triage
description: "Invoke on a RED validate gate — classify every failing DoD as regression or gap, surface the next-fix target for human decision. Entered from stage `validate` (on red) or stage `human` (when a human finds a gap). Produces `releases/<id>/triage-log.md`. Do NOT invoke to write code — that's stage `develop`. Use when the user says 'triage', 'classify the failures', 'what broke', 'regression or gap', or after `release-validate` fails. INNER-loop exit seam."
---

## Stage 07 — triage

See `tests3/stages/07-triage.md` for full stage contract (objectives, inputs, outputs, exit, may-not-do).

## First action — ALWAYS

```bash
python3 tests3/lib/stage.py assert-is triage
```

If not in `triage`, halt. Transition is automatic: `validate` on red → `triage` (`stage.enter triage` from the Makefile).

## Steps

1. Read `tests3/reports/release-<tag>.md` — enumerate every DoD with status ≠ `pass`.
2. For each failing DoD:
   - **regression**: existing code path broken. Cite bound check, expected vs actual, touched commits (use `git log --oneline -20` on files in the check's coverage).
   - **gap**: the test itself is unreliable. Cite root cause (race, timing, infra fragility, misowned DoD). NEVER classify as "flake".
3. Write `releases/<id>/triage-log.md` — one entry per failing DoD with classification + rationale + next step.
4. HALT. Present to human. Human writes `fix this first: <DoD-id>` or `accept this gap, do not fix` in the log.

## Output shape — `triage-log.md`

```markdown
# Triage — <release-id>

## <DoD-id>  [REGRESSION | GAP]
**status:** fail in <modes>
**bound check:** <check-id> (from registry.yaml)
**symptom:** <from report message>
**root cause hypothesis:** <your analysis>
**proposed fix:** <specific code change or infra/test change>
**touched commits:** <sha-list>

<!-- human adds below this line -->
fix this first: <yes|no>
```

## May NOT
- Edit any code (code editing is stage `develop`).
- Run tests, rebuild images, re-provision.
- Classify a failure as "flake" without root-cause analysis.
- Advance stage without human confirmation.

## Exit

`triage-log.md` exists AND contains a human-written directive for every failing DoD.

## Next

`develop` — usual path (human picks a next-fix target → implement fix → validate again).
`human` — rare (all failures are accepted gaps).
