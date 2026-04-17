---
name: 5-iterate
description: "Invoke during the Vexa release dev loop — run the scope-filtered tests against the live deployments to see if the current fix works, with a fast turnaround (~2-3 min). Use when the user says 'run the targeted tests', 'check if it works', 'iterate', 'validate the fix', 'quick test', 'did this land', or any inner dev-test loop between commits. Not a merge gate (stage 6 is)."
---

## Stage 5 of 9 — iterate

Runs **only** the tests the scope's `proves[]` references for each mode. Produces a scope-status report so you can see exactly which issue is still red. Loop this step until every `required_modes` across every issue is green. Then move to stage 6 (authoritative full pass).

## Command

```bash
make release-iterate SCOPE=$SCOPE
```

## What it does

For each mode in `scope.deployments.modes`:

1. SSH / kubectl to the deployment.
2. Invoke `make -C tests3 validate-<mode> SCOPE=$SCOPE` — `run-matrix.sh` filters the test registry to just the tests that appear in `scope.issues[].proves[]` for that mode (plus all `smoke-*` tiers when a `check:` is referenced, since checks live inside smoke tiers).
3. Each test writes `.state-<mode>/reports/<mode>/<test>.json`.
4. Pull reports back to the host.

Then `release-report` aggregates them into `tests3/reports/release-<tag>.md` with a **Scope status** section listing each issue, its required modes, and the per-proof verdict.

## Dev loop

```
edit code → git commit → git push origin dev → make release-deploy SCOPE=$SCOPE → make release-iterate SCOPE=$SCOPE → read report
```

~2-3 min per loop when tests are cheap. Reports are cumulative — re-running overwrites the per-mode JSONs.

## Reading the report

The top of `tests3/reports/release-<tag>.md` has the **Scope status** table:

```
| Issue | Required modes | Status per proof | Verdict |
| webhook-status-fast-path | compose | compose webhooks/e2e_status: ❌ fail | ❌ fail |
```

Below it, **Feature confidence** and per-deployment raw test results. Features with `strict_features` in the scope require 100% — others use their README's `gate.confidence_min`.

Feature READMEs also auto-update (via `aggregate.py --write-features`) between `<!-- BEGIN AUTO-DOD -->` markers. Idempotent.

## Stopping criterion

Every issue's verdict in the Scope status table is ✅ pass (or ⚠️ skip, if a proof is correctly tier-skipped in a mode without svc_exec etc.).

If an issue stays red after multiple rounds: stop iterating, re-read the hypothesis. If the hypothesis is wrong, close this cycle and go back to stage 1 with a new scope.

## Important

- Iterate does **not** gate merge. It's the dev feedback loop. Full (stage 6) is the gate.
- State is **not** reset between iterations. If a bug reproduces only on first-boot data, you won't catch it here — that's why stage 6 fresh-resets.

## Next

Once iterate is green on every required mode:
→ stage 6: `make release-full SCOPE=$SCOPE` — fresh-reset every deployment and run the full cheap-tier matrix.
