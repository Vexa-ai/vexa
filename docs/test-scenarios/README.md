# test-scenarios — the seam/module scenario catalog

One concern: a committed, behavior-named catalog of the failure scenarios the 0.12 hardening campaign
pins as repeatable seam/module tests. `meeting-seams.md` is the source of truth — one row per known
failure class, each naming its `module_probe` / `seam_probe` (and optional `compose_probe` / `live_probe`)
and the EXACT expected contract state. The catalog is the campaign checklist + standing anti-regression
ledger (see `docs/MATURITY-FINDINGS.md` and the campaign plan).

Depends on: nothing (documentation). Consumed by humans + the autonomous hardening session.
