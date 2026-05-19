# Scanner Coverage

- `semgrep`: ok (exit=0, findings=35, blockers=0)
  - Rationale: Broad multi-language SAST and custom bad-pattern coverage.
  - Trigger: default tool for passive release scan
- `gitleaks`: ok (exit=0, findings=0, blockers=0)
  - Rationale: Secret scanning for current release source; local env files and generated build outputs are excluded by policy.
  - Trigger: default tool for passive release scan
- `trivy`: ok (exit=0, findings=0, blockers=0)
  - Rationale: Filesystem dependency, IaC, secret, and vulnerability scan.
  - Trigger: default tool for passive release scan
- `osv-scanner`: ok (exit=0, findings=0, blockers=0)
  - Rationale: Known vulnerable dependency detection from OSV advisories for lockfile ecosystems; Python requirements are gated by pip-audit.
  - Trigger: default tool for passive release scan
- `syft`: ok (exit=0, findings=0, blockers=0)
  - Rationale: SBOM inventory for release and vulnerability correlation.
  - Trigger: default tool for passive release scan
- `zizmor`: ok (exit=0, findings=0, blockers=0)
  - Rationale: GitHub Actions supply-chain and workflow risk detection.
  - Trigger: matched .github/workflows
- `actionlint`: ok (exit=0, findings=0, blockers=0)
  - Rationale: GitHub Actions syntax and workflow expression validation.
  - Trigger: matched .github/workflows
- `bandit`: ok (exit=1, findings=131, blockers=0)
  - Rationale: Python security bad-pattern scan.
  - Trigger: matched python
- `pip-audit`: ok (exit=0, findings=0, blockers=0)
  - Rationale: Python dependency vulnerability scan.
  - Trigger: matched pip

## Finding Counts

- `Semgrep OSS`: 35
- `bandit`: 131
