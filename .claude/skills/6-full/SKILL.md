---
name: 6-full
description: "Invoke when the user wants to run the authoritative Vexa release validation — fresh-reset every deployment, run the FULL cheap-tier test matrix on every mode, aggregate into a committed report, and gate on per-feature confidence. This is the automated merge gate. Only unblocks the ship stage if every feature meets its confidence_min and no required-mode issue fails. Use when the user says 'run the full gate', 'authoritative validation', 'reset and run everything', 'final validation', 'run the matrix', or after stage 5 (iterate) is green."
---

## Stage 6 of 9 — full (automated gate)

Authoritative pass. Iterate (stage 5) proved the fix works in dirty state. Full resets every deployment to a clean first-boot, redeploys the latest `:dev`, and runs every cheap-tier test on every provisioned mode. Gates merge.

## Command

```bash
make release-full SCOPE=$SCOPE
```

## What it does

1. **Reset every mode** in parallel:
   - `lite` → stop + remove `vexa-lite` + `vexa-postgres`, drop volumes, re-run `make lite`.
   - `compose` → `docker compose down -v` then `docker compose up -d --pull always`.
   - `helm` → `helm uninstall`, delete PVCs, reinstall via `lke-setup-helm.sh`.

2. **Wait for services** to become healthy (gateway responds on `:8056`).

3. **Run the full cheap-tier matrix** per mode:
   - `smoke-static`, `smoke-env`, `smoke-health`, `smoke-contract`
   - `webhooks`, `containers`, `dashboard-auth`
   - `dashboard-proxy` (compose + helm)
   - Every test in `tests3/test-registry.yaml` where `tier: cheap` and `mode ∈ runs_in`.

4. **Aggregate** → `tests3/reports/release-<tag>.md`:
   - Scope status section (per-issue, per-mode verdict).
   - Per-feature confidence table.
   - Raw test results per mode.
   - Also rewrites `features/*/README.md` DoD tables (idempotent).

5. **Gate-check**. Exits non-zero if:
   - Any feature's confidence is below its `gate.confidence_min` in the README frontmatter.
   - Any feature in `scope.strict_features` is below 100%.
   - Any `scope.issues[]` has a `fail` status in one of its `required_modes`.

## Why fresh reset

Iterate (stage 5) keeps state across runs — fast feedback, but hides first-boot regressions (migrations, seed data, init containers, fresh consumer-group creation). Full wipes everything so "works on a clean install" is proven, not assumed.

Reset takes ~30s per mode. Full matrix takes 2-5 min per mode. Total: well under 10 min for three modes in parallel.

## Reading the gate output

Pass:
```
  Release gate PASSED. Report → tests3/reports/release-0.10.0-260417-1830.md
```

Fail:
```
GATE FAILED (3):
  - webhooks: 70% < 95% [strict]
  - infrastructure: 90% < 100% [strict]
  - issue webhook-status-fast-path: fail in required mode 'compose' on webhooks/e2e_status
```

Open the report; the Scope status table points directly at the failing proof.

## If the gate fails

1. Read the report. Find the failing proof.
2. **Do not** workaround by editing the test or loosening the gate. The automated gate is the user's contract.
3. Back to stage 3 (develop). Commit a fix. Push. `make release-deploy`. Run stage 5 (iterate) to confirm. Re-run stage 6 only once iterate is green.

## Next

Once `release-full` exits 0:
→ stage 7: `make release-human-sheet SCOPE=$SCOPE` — generate the human checklist.
