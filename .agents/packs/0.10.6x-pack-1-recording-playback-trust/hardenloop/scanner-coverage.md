# Scanner Coverage

- `semgrep`: error (exit=127, findings=0, blockers=0)
  - Rationale: Release-scoped multi-language SAST against findings introduced after the branch baseline; full repo hardening debt is tracked separately.
  - Trigger: default tool for passive release scan
- `gitleaks`: ok (exit=1, findings=0, blockers=0)
  - Rationale: Secret scan current tracked source plus local tracked diff, without historical or ignored local artifacts.
  - Trigger: default tool for passive release scan
- `trivy`: missing (trivy not installed., findings=0, blockers=0)
  - Rationale: Filesystem dependency, IaC, secret, and vulnerability scan.
  - Trigger: default tool for passive release scan
- `osv-scanner`: missing (osv-scanner not installed., findings=0, blockers=0)
  - Rationale: Known vulnerable dependency detection from OSV advisories.
  - Trigger: default tool for passive release scan
- `syft`: missing (syft not installed., findings=0, blockers=0)
  - Rationale: SBOM inventory for release and vulnerability correlation.
  - Trigger: default tool for passive release scan
- `zizmor`: missing (zizmor not installed., findings=0, blockers=0)
  - Rationale: GitHub Actions supply-chain and workflow risk detection.
  - Trigger: matched .github/workflows
- `actionlint`: missing (actionlint not installed., findings=0, blockers=0)
  - Rationale: GitHub Actions syntax and workflow expression validation.
  - Trigger: matched .github/workflows
- `bandit`: ok (exit=1, findings=130, blockers=0)
  - Rationale: Python security bad-pattern scan.
  - Trigger: matched python
- `pip-audit`: missing (pip-audit not installed., findings=0, blockers=0)
  - Rationale: Python dependency vulnerability scan.
  - Trigger: matched pip

## Finding Counts

- `bandit`: 130
