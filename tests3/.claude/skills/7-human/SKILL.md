# ⚠ STALE 2026-05-12 — descriptions reference the OLD state machine (idle→groom→plan→develop→provision→deploy→validate→triage→human→ship→teardown). The current model has 13 stages with do→audit→human pattern at three levels. Authoritative: tests3/stages/release-flow.md + per-stage 00-idle.md…12-teardown.md. Full skill-directory rewrite is v0.10.7 pack state-machine-docs-rewrite.

---
name: 7-human
description: "Stage 08 (human): (A) code review packet + (B) bounded manual eyeroll. TWO modes: generate/regenerate the checklist OR translate a human bug report (plain English, URL, screenshot) into a formal `release-issue-add` call. The human describes; the agent derives GAP + NEW_CHECKS and executes. Use when the user says 'human checklist', 'generate the sheet', 'sign off', 'gate', or reports any failure while stepping through the checklist."
---

## Stage 08 — human

See `tests3/stages/08-human.md` for the full stage contract.

## First action — ALWAYS

```bash
python3 tests3/lib/stage.py assert-is human
```

Legal predecessor: `validate` (green Gate).

## Part A — Code review

Generate `releases/<id>/code-review.md` with:
- **Per-commit summary**: what + why + risk + touched DoDs.
- **Diffs grouped by concern**, not git order.
- **Risk notes**: invariants, ordering deps, anything a fast reviewer might miss.
- **Open questions** for the human.

Human reads, flips `code_review_approved: true` in `human-approval.yaml`. Part B unlocks.

## Part B — Bounded eyeroll

Generate `releases/<id>/human-checklist.md` — union of:
- `tests3/human-always.yaml` accumulated items
- scope's `human_verify[]`
- URLs / env / assets pre-resolved

Human ticks each `- [ ]` → `- [x]`.

## If the human reports a failure

**The human describes; the agent does the filing.** Derive every field yourself:

1. Reproduce / confirm by inspection.
2. Derive `ID` (kebab-case), `PROBLEM` (1-sentence), `HYPOTHESIS`, `GAP` (why automation missed it), `NEW_CHECKS` (registry IDs or `test:step`), `MODES`, `HV_*`.
3. Execute:

```bash
make release-issue-add \
  SCOPE=releases/<id>/scope.yaml \
  ID=<slug> SOURCE=human \
  PROBLEM="…" HYPOTHESIS="…" \
  GAP="…" NEW_CHECKS="…,…" \
  MODES=compose HV_MODE=compose HV_DO="…" HV_EXPECT="…"
```

The helper refuses if `GAP` or `NEW_CHECKS` is empty. NEVER ask the human to fill them in.

4. Transition to `triage` — the fix needs to loop through develop → deploy → validate.

## May NOT

- Edit code.
- Change infra.
- Skip code review.
- Auto-sign either part.
- Ask the human to fill in `GAP` / `NEW_CHECKS` / structured fields.

## Next

`ship` — both parts signed, no unresolved findings.
`triage` — human found a gap (either part).
