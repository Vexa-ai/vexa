# Hardenloop State

Pack: `0.10.6x-pack-7-release-identity-packaging-hardening`
Command shape: `hardenloop release . --mode oss-release --cycles 1 --fix none --out .agents/packs/0.10.6x-pack-7-release-identity-packaging-hardening/hardenloop/run --config <vexa-release.toml> --timeout-seconds 180`

Decision: `incomplete_coverage`
Release blockers: `0`

Scanner coverage:

- Ran: bandit.
- Error: semgrep, gitleaks exited `127`.
- Missing: trivy, osv-scanner, syft, zizmor, actionlint, pip-audit.

Evidence:

- `hardenloop/run/hardenloop-attestation.json`
- `hardenloop/run/release-blockers.md`
- `hardenloop/run/scanner-coverage.md`
- `hardenloop/run/scanner-execution-log.md`

Private advisory/raw finding payloads were generated locally by Hardenloop but are intentionally omitted from committed PR evidence to avoid leaking sensitive-looking snippets from the broader repository.

Pack advancement:

The hardening gate ran successfully and found no normalized release blockers. Advancement is acceptable only with the explicit coverage caveat above; final release hardening should rerun with the missing scanners installed.
