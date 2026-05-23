# PACK 7 Review

Pack: `0.10.6x-pack-7-release-identity-packaging-hardening`
Issue: https://github.com/Vexa-ai/vexa/issues/362
Branch: `codex/pack-0.10.6x-pack-7-release-identity-packaging-hardening`
Worktree: `/home/dima/dev/vexa-pack-0.10.6x-pack-7-release-identity-packaging-hardening`

## Scope Delivered

- Release identity now converges on `0.10.6.2.1` across `VERSION`, Helm `appVersion`, Helm chart SemVer, dashboard release generation, Compose dashboard build args, and Lite/dashboard Dockerfile identity checks.
- Dashboard and Lite image builds copy canonical `VERSION` and Helm `Chart.yaml` into `/repo`, set `VEXA_REPO_ROOT=/repo`, and run `npm run assert-release-version` after the dashboard build.
- Helm bot image resolution uses one `vexa.botImage` helper for meeting-api and runtime-api bot image envs, so a release image tag consistently selects `vexaai/vexa-bot:<tag>`.
- Release hardening changes pin GitHub Actions, add least-privilege workflow permissions, preserve non-root service readability, and raise dependency floors/lockfiles for the release candidate.
- The branch intentionally excludes `tests3`, product behavior packs, notebooks, docs-only auth examples, and runtime backend behavior outside the packaging/image identity surface.

## Validation

- Source identity proof: pass after one logged shell-pattern retry. Evidence: `tests/source-identity-proof.json`.
- Dashboard release generator/assertion synthetic: pass. Evidence: `tests/dashboard-release-version.generated.json`.
- Meeting API security headers pytest: pass under Python 3.11 with meeting-api requirements. Earlier Python 3.9 and missing-test-dependency attempts are retained in `ops/ops.jsonl`.
- Helm lint/template: pass; rendered bot image helper with `vexaai/vexa-bot:0.10.6.2.1`. Evidence: `tests/helm-template.yaml`.
- Compose gate: pass via `docker compose config` using allocated pack ports `44460` and `44461`, with `IMAGE_TAG=0.10.6.2.1` and `VEXA_VERSION=0.10.6.2.1`. Evidence: `compose/compose-config.yaml`.
- Lite gate: source-level identity proof pass; runtime image validation is deferred until product-pack stitching per the pack epic. Evidence: `lite/lite-identity-proof.json`.
- NPM lock consistency: pass via dry-run `npm ci` for transcript-rendering, dashboard, and vexa-bot package locks.
- Live/human validation: not required for this pack directly per epic.

## Hardenloop

- Command completed with `--fix none`.
- Decision: `incomplete_coverage`.
- Release blockers: `0`.
- Coverage caveat: Semgrep and gitleaks returned exit `127`; trivy, osv-scanner, syft, zizmor, actionlint, and pip-audit were not installed. Bandit ran and produced existing broader-repo findings with no normalized release blockers.
- Evidence: `hardenloop/run/hardenloop-attestation.json`, `hardenloop/run/release-blockers.md`, `hardenloop/run/scanner-coverage.md`.
- Private advisory/raw finding payloads were generated locally and intentionally omitted from committed PR evidence to avoid leaking sensitive-looking snippets.

## Residual Risk

- Full Lite image runtime validation is intentionally deferred until stitched candidate/product-pack integration because the epic defines that gate as after product pack stitching.
- Hardenloop coverage is incomplete due missing local scanner tools. The pack may advance with this explicit caveat, but final release hardening should rerun with full scanner availability.
