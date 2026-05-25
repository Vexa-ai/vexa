# Scanner Execution Log

- Target: `/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`
- Started: 2026-05-23T15:18:30.163566+00:00
- Mode: passive only

## semgrep

- Rationale: Release-scoped multi-language SAST against findings introduced after the branch baseline; full repo hardening debt is tracked separately.
- Categories: sast
- Trigger: default tool for passive release scan
- Command: `bash -lc 'set -euo pipefail; target='\''/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence'\''; raw='\''/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw'\''; base=$(git -C "$target" merge-base HEAD main 2>/dev/null || git -C "$target" rev-parse HEAD~1); cd "$target"; semgrep scan --config auto --sarif -o "$raw/semgrep.sarif" --baseline-commit "$base" --exclude services/dashboard/.next .'`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/semgrep.sarif`
- Result: error (exit=127)
- Duration: 12 ms

## gitleaks

- Rationale: Secret scan current tracked source plus local tracked diff, without historical or ignored local artifacts.
- Categories: secrets
- Trigger: default tool for passive release scan
- Command: `bash -lc 'set -euo pipefail; target='\''/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence'\''; raw='\''/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw'\''; tmp=$(mktemp -d); patch=$(mktemp); trap '\''rm -rf "$tmp" "$patch"'\'' EXIT; git -C "$target" archive HEAD | tar -x -C "$tmp"; git -C "$target" diff --binary > "$patch"; if [ -s "$patch" ]; then (cd "$tmp" && git apply "$patch"); fi; gitleaks detect --source "$tmp" --no-git --redact --report-format json --report-path "$raw/gitleaks.json" --no-banner'`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/gitleaks.json`
- Result: error (exit=127)
- Duration: 122 ms

## trivy

- Rationale: Filesystem dependency, IaC, secret, and vulnerability scan.
- Categories: sca, iac, secrets
- Trigger: default tool for passive release scan
- Command: `trivy fs --skip-dirs .git --skip-dirs services/dashboard/.next --format sarif --output /home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/trivy.sarif /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/trivy.sarif`
- Result: missing (trivy not installed.)

## osv-scanner

- Rationale: Known vulnerable dependency detection from OSV advisories.
- Categories: sca
- Trigger: default tool for passive release scan
- Command: `osv-scanner scan source -r /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence --no-resolve --experimental-exclude services/dashboard/.next --format json`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/osv.json`
- Result: missing (osv-scanner not installed.)

## syft

- Rationale: SBOM inventory for release and vulnerability correlation.
- Categories: sbom
- Trigger: default tool for passive release scan
- Command: `syft /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence -o cyclonedx-json`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/sbom.cyclonedx.json`
- Result: missing (syft not installed.)

## zizmor

- Rationale: GitHub Actions supply-chain and workflow risk detection.
- Categories: ci-cd
- Trigger: matched .github/workflows
- Command: `zizmor --format sarif /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/zizmor.sarif`
- Result: missing (zizmor not installed.)

## actionlint

- Rationale: GitHub Actions syntax and workflow expression validation.
- Categories: ci-cd
- Trigger: matched .github/workflows
- Command: `actionlint -format '{{json .}}'`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/actionlint.json`
- Result: missing (actionlint not installed.)

## bandit

- Rationale: Python security bad-pattern scan.
- Categories: sast
- Trigger: matched python
- Command: `bandit -r . -x .git,.claude,.codex,.venv,venv,node_modules,dist,build,tests,services/dashboard/.next -f json -o /home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/bandit.json`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/bandit.json`
- Result: ok (exit=1)
- Duration: 2402 ms

## pip-audit

- Rationale: Python dependency vulnerability scan.
- Categories: sca
- Trigger: matched pip
- Command: `pip-audit --path /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence --format json`
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/pip-audit.json`
- Result: missing (pip-audit not installed.)

## Normalization Summary

- Normalized findings: 127
- Release blockers: 0

## Tool Outcomes

### semgrep

- Status: error
- Rationale: Release-scoped multi-language SAST against findings introduced after the branch baseline; full repo hardening debt is tracked separately.
- Trigger: default tool for passive release scan
- Detail: exit=127
- Started: 2026-05-23T15:18:30.164456+00:00
- Duration ms: 12
- Exit code: 127
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/semgrep.sarif`
- Command: `bash -lc 'set -euo pipefail; target='\''/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence'\''; raw='\''/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw'\''; base=$(git -C "$target" merge-base HEAD main 2>/dev/null || git -C "$target" rev-parse HEAD~1); cd "$target"; semgrep scan --config auto --sarif -o "$raw/semgrep.sarif" --baseline-commit "$base" --exclude services/dashboard/.next .'`

### gitleaks

- Status: error
- Rationale: Secret scan current tracked source plus local tracked diff, without historical or ignored local artifacts.
- Trigger: default tool for passive release scan
- Detail: exit=127
- Started: 2026-05-23T15:18:30.177341+00:00
- Duration ms: 122
- Exit code: 127
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/gitleaks.json`
- Command: `bash -lc 'set -euo pipefail; target='\''/home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence'\''; raw='\''/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw'\''; tmp=$(mktemp -d); patch=$(mktemp); trap '\''rm -rf "$tmp" "$patch"'\'' EXIT; git -C "$target" archive HEAD | tar -x -C "$tmp"; git -C "$target" diff --binary > "$patch"; if [ -s "$patch" ]; then (cd "$tmp" && git apply "$patch"); fi; gitleaks detect --source "$tmp" --no-git --redact --report-format json --report-path "$raw/gitleaks.json" --no-banner'`

### trivy

- Status: missing
- Rationale: Filesystem dependency, IaC, secret, and vulnerability scan.
- Trigger: default tool for passive release scan
- Detail: trivy not installed.
- Started: not executed
- Duration ms: n/a
- Exit code: n/a
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/trivy.sarif`
- Command: `trivy fs --skip-dirs .git --skip-dirs services/dashboard/.next --format sarif --output /home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/trivy.sarif /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`

### osv-scanner

- Status: missing
- Rationale: Known vulnerable dependency detection from OSV advisories.
- Trigger: default tool for passive release scan
- Detail: osv-scanner not installed.
- Started: not executed
- Duration ms: n/a
- Exit code: n/a
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/osv.json`
- Command: `osv-scanner scan source -r /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence --no-resolve --experimental-exclude services/dashboard/.next --format json`

### syft

- Status: missing
- Rationale: SBOM inventory for release and vulnerability correlation.
- Trigger: default tool for passive release scan
- Detail: syft not installed.
- Started: not executed
- Duration ms: n/a
- Exit code: n/a
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/sbom.cyclonedx.json`
- Command: `syft /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence -o cyclonedx-json`

### zizmor

- Status: missing
- Rationale: GitHub Actions supply-chain and workflow risk detection.
- Trigger: matched .github/workflows
- Detail: zizmor not installed.
- Started: not executed
- Duration ms: n/a
- Exit code: n/a
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/zizmor.sarif`
- Command: `zizmor --format sarif /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence`

### actionlint

- Status: missing
- Rationale: GitHub Actions syntax and workflow expression validation.
- Trigger: matched .github/workflows
- Detail: actionlint not installed.
- Started: not executed
- Duration ms: n/a
- Exit code: n/a
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/actionlint.json`
- Command: `actionlint -format '{{json .}}'`

### bandit

- Status: ok
- Rationale: Python security bad-pattern scan.
- Trigger: matched python
- Detail: exit=1
- Started: 2026-05-23T15:18:30.301850+00:00
- Duration ms: 2402
- Exit code: 1
- Findings: 127
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/bandit.json`
- Command: `bandit -r . -x .git,.claude,.codex,.venv,venv,node_modules,dist,build,tests,services/dashboard/.next -f json -o /home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/bandit.json`

### pip-audit

- Status: missing
- Rationale: Python dependency vulnerability scan.
- Trigger: matched pip
- Detail: pip-audit not installed.
- Started: not executed
- Duration ms: n/a
- Exit code: n/a
- Findings: 0
- Release blockers: 0
- Raw output: `/home/dima/dev/vexa-agents-pack-pipeline-strategy/.agents/packs/0.10.6x-pack-4-stop-delete-lifecycle-convergence/hardenloop/run/raw/pip-audit.json`
- Command: `pip-audit --path /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence --format json`
