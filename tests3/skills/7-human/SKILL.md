---
name: 7-human
description: "Invoke for the human-validation stage of a release. Two sub-modes: (a) generate/regenerate the checklist and gate on it; (b) translate human bug reports (plain English, screenshots, URLs) into a formal `release-issue-add` call. The human describes; the agent fills in GAP + NEW_CHECKS + modes + proves bindings and executes. Use when the user says 'human checklist', 'generate the sheet', 'sign off', 'gate', or reports any failure while stepping through the checklist — 'X is broken on compose', 'look at /webhooks', 'it shows wrong stuff'."
---

## Stage 6 — human validation

The human clicks through the checklist. The agent handles protocol mechanics.

## Commands the agent runs

```bash
make release-human-sheet SCOPE=$SCOPE          # first generate
make release-human-sheet SCOPE=$SCOPE --force  # regenerate (preserves ticks)
make release-human-gate   SCOPE=$SCOPE         # gate — exits non-zero if any [ ] remains
```

## When the human reports a failure

**The human's job is to describe what broke.** Examples:

> "the /webhooks page on compose only shows meeting.completed"
> "helm /meetings is all red"
> "lite login gives 500 after an hour"

**The agent's job is to file the issue.** Do NOT ask the human for gap/checks/modes — derive them:

1. **Reproduce / confirm** by inspection (curl, `kubectl logs`, DB query) — one-shot verification that the observation is real.
2. **Derive fields** yourself:
   - `ID` — kebab-case slug from the symptom.
   - `PROBLEM` — one sentence, what the human observed, including the specific URL/command.
   - `HYPOTHESIS` — your best guess at root cause.
   - `GAP` — "why didn't the automated matrix catch this?" Point at the missing test/step/check. If there is no gap (an existing check already fails), still record it: "check X is already failing but required_modes excluded this mode" or similar.
   - `NEW_CHECKS` — ID(s) of the regression check(s) that will catch this next time. If a suitable check exists in the registry, use its ID. If not, invent a new ID (UPPER_SNAKE for registry check, `test:step` for a test step) — you will implement it in stage 3.
   - `MODES` — the deployments this affects. If unsure, use scope.deployments.modes.
   - `HV_MODE` + `HV_DO` + `HV_EXPECT` — the smallest repro a future human can run.
3. **Show the call in one line** and execute it. Example:

```bash
make release-issue-add \
  SCOPE=$SCOPE ID=dashboard-webhooks-ui-rollup SOURCE=human \
  PROBLEM="/webhooks on compose shows only meeting.completed rows" \
  HYPOTHESIS="Dashboard route reads webhook_delivery (singular) not webhook_deliveries[]" \
  GAP="webhooks.sh never hits the dashboard /api/webhooks/deliveries route" \
  NEW_CHECKS="DASHBOARD_WEBHOOKS_ALL_EVENT_TYPES" \
  MODES=compose \
  HV_MODE=compose \
  HV_DO="open http://{vm_ip}:3001/webhooks after a run with status events" \
  HV_EXPECT="table rows include meeting.started / meeting.status_change not only meeting.completed"
```

The command refuses if GAP or NEW_CHECKS are empty — that's the protocol enforcement; the agent must fill them in, not ask the human to.

4. **After filing**, move to stage 3 (develop) to implement the check + any code fix, stage 4 (deploy), stage 5 (test). Regenerate the checklist with `--force` (ticks auto-preserve). Hand back to the human only the new item to verify.

## Loop cap

3 rounds of human-found bugs in one cycle → the scope is wrong. Go back to stage 1 and split.

## What the agent never does

- Ask the human to fill in `GAP` / `NEW_CHECKS` / `MODES` / structured fields.
- Edit `scope.yaml` by hand instead of calling `release-issue-add`.
- Narrow `required_modes` to dodge a failing check.
- Skip re-running stage 5 before resuming the checklist.
