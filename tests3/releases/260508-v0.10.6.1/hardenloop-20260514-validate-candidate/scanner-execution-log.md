# Scanner Execution Log

- Target: `/home/dima/dev/vexa-260508-v0.10.6.1`
- Started: 2026-05-14T15:29:30.594599+00:00
- Mode: passive only

## semgrep

- Rationale: Broad multi-language SAST and custom bad-pattern coverage.
- Categories: sast
- Trigger: default tool for passive release scan
- Command: `semgrep scan --config auto --sarif -o /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/semgrep.sarif /home/dima/dev/vexa-260508-v0.10.6.1`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/semgrep.sarif`
- Result: ok (exit=0)
- Duration: 14447 ms

## gitleaks

- Rationale: Secret scanning for current release source; local env files and generated build outputs are excluded by policy.
- Categories: secrets
- Trigger: default tool for passive release scan
- Command: `gitleaks dir /home/dima/dev/vexa-260508-v0.10.6.1 --config /home/dima/dev/vexa-260508-v0.10.6.1/tests3/gitleaks-release.toml --report-format json --report-path /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/gitleaks.json --no-banner`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/gitleaks.json`
- Result: ok (exit=0)
- Duration: 9831 ms

## trivy

- Rationale: Filesystem dependency, IaC, secret, and vulnerability scan.
- Categories: sca, iac, secrets
- Trigger: default tool for passive release scan
- Command: `trivy fs --format sarif --output /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/trivy.sarif /home/dima/dev/vexa-260508-v0.10.6.1`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/trivy.sarif`
- Result: ok (exit=0)
- Duration: 4311 ms

## osv-scanner

- Rationale: Known vulnerable dependency detection from OSV advisories for lockfile ecosystems; Python requirements are gated by pip-audit.
- Categories: sca
- Trigger: default tool for passive release scan
- Command: `osv-scanner scan source --lockfile /home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/package-lock.json --lockfile /home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/package-lock.json --lockfile /home/dima/dev/vexa-260508-v0.10.6.1/packages/transcript-rendering/package-lock.json --format json`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/osv.json`
- Result: ok (exit=0)
- Duration: 3149 ms

## syft

- Rationale: SBOM inventory for release and vulnerability correlation.
- Categories: sbom
- Trigger: default tool for passive release scan
- Command: `syft /home/dima/dev/vexa-260508-v0.10.6.1 -o cyclonedx-json`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/sbom.cyclonedx.json`
- Result: ok (exit=0)
- Duration: 4454 ms

## zizmor

- Rationale: GitHub Actions supply-chain and workflow risk detection.
- Categories: ci-cd
- Trigger: matched .github/workflows
- Command: `zizmor --format sarif /home/dima/dev/vexa-260508-v0.10.6.1`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/zizmor.sarif`
- Result: ok (exit=0)
- Duration: 87 ms

## actionlint

- Rationale: GitHub Actions syntax and workflow expression validation.
- Categories: ci-cd
- Trigger: matched .github/workflows
- Command: `actionlint -format '{{json .}}'`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/actionlint.json`
- Result: ok (exit=0)
- Duration: 62 ms

## bandit

- Rationale: Python security bad-pattern scan.
- Categories: sast
- Trigger: matched python
- Command: `bandit -r . -x .git,.claude,.codex,.venv,venv,node_modules,dist,build,tests,tests3 -f json -o /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/bandit.json`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/bandit.json`
- Result: ok (exit=1)
- Duration: 2950 ms

## pip-audit

- Rationale: Python dependency vulnerability scan.
- Categories: sca
- Trigger: matched pip
- Command: `pip-audit --path /home/dima/dev/vexa-260508-v0.10.6.1 --format json`
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/pip-audit.json`
- Result: ok (exit=0)
- Duration: 630 ms

## Normalization Summary

- Normalized findings: 166
- Release blockers: 0

## Tool Outcomes

### semgrep

- Status: ok
- Rationale: Broad multi-language SAST and custom bad-pattern coverage.
- Trigger: default tool for passive release scan
- Detail: exit=0
- Started: 2026-05-14T15:29:30.595490+00:00
- Duration ms: 14447
- Exit code: 0
- Findings: 35
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/semgrep.sarif`
- Command: `semgrep scan --config auto --sarif -o /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/semgrep.sarif /home/dima/dev/vexa-260508-v0.10.6.1`

### gitleaks

- Status: ok
- Rationale: Secret scanning for current release source; local env files and generated build outputs are excluded by policy.
- Trigger: default tool for passive release scan
- Detail: exit=0
- Started: 2026-05-14T15:29:45.043055+00:00
- Duration ms: 9831
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/gitleaks.json`
- Command: `gitleaks dir /home/dima/dev/vexa-260508-v0.10.6.1 --config /home/dima/dev/vexa-260508-v0.10.6.1/tests3/gitleaks-release.toml --report-format json --report-path /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/gitleaks.json --no-banner`

### trivy

- Status: ok
- Rationale: Filesystem dependency, IaC, secret, and vulnerability scan.
- Trigger: default tool for passive release scan
- Detail: exit=0
- Started: 2026-05-14T15:29:54.874426+00:00
- Duration ms: 4311
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/trivy.sarif`
- Command: `trivy fs --format sarif --output /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/trivy.sarif /home/dima/dev/vexa-260508-v0.10.6.1`

### osv-scanner

- Status: ok
- Rationale: Known vulnerable dependency detection from OSV advisories for lockfile ecosystems; Python requirements are gated by pip-audit.
- Trigger: default tool for passive release scan
- Detail: exit=0
- Started: 2026-05-14T15:29:59.186650+00:00
- Duration ms: 3149
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/osv.json`
- Command: `osv-scanner scan source --lockfile /home/dima/dev/vexa-260508-v0.10.6.1/services/dashboard/package-lock.json --lockfile /home/dima/dev/vexa-260508-v0.10.6.1/services/vexa-bot/package-lock.json --lockfile /home/dima/dev/vexa-260508-v0.10.6.1/packages/transcript-rendering/package-lock.json --format json`

### syft

- Status: ok
- Rationale: SBOM inventory for release and vulnerability correlation.
- Trigger: default tool for passive release scan
- Detail: exit=0
- Started: 2026-05-14T15:30:02.336203+00:00
- Duration ms: 4454
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/sbom.cyclonedx.json`
- Command: `syft /home/dima/dev/vexa-260508-v0.10.6.1 -o cyclonedx-json`

### zizmor

- Status: ok
- Rationale: GitHub Actions supply-chain and workflow risk detection.
- Trigger: matched .github/workflows
- Detail: exit=0
- Started: 2026-05-14T15:30:06.795299+00:00
- Duration ms: 87
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/zizmor.sarif`
- Command: `zizmor --format sarif /home/dima/dev/vexa-260508-v0.10.6.1`

### actionlint

- Status: ok
- Rationale: GitHub Actions syntax and workflow expression validation.
- Trigger: matched .github/workflows
- Detail: exit=0
- Started: 2026-05-14T15:30:06.883505+00:00
- Duration ms: 62
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/actionlint.json`
- Command: `actionlint -format '{{json .}}'`

### bandit

- Status: ok
- Rationale: Python security bad-pattern scan.
- Trigger: matched python
- Detail: exit=1
- Started: 2026-05-14T15:30:06.946031+00:00
- Duration ms: 2950
- Exit code: 1
- Findings: 131
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/bandit.json`
- Command: `bandit -r . -x .git,.claude,.codex,.venv,venv,node_modules,dist,build,tests,tests3 -f json -o /home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/bandit.json`

### pip-audit

- Status: ok
- Rationale: Python dependency vulnerability scan.
- Trigger: matched pip
- Detail: exit=0
- Started: 2026-05-14T15:30:09.896591+00:00
- Duration ms: 630
- Exit code: 0
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-260508-v0.10.6.1/tests3/releases/260508-v0.10.6.1/hardenloop-20260514-validate-candidate/raw/pip-audit.json`
- Command: `pip-audit --path /home/dima/dev/vexa-260508-v0.10.6.1 --format json`
