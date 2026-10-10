# security — assessment evidence

Dated security-assessment artifacts for the project, kept in-repo so reviewers can verify claims
without running anything.

- [`osps-baseline/`](osps-baseline/) — [OSPS Baseline](https://baseline.openssf.org/) scanner
  self-assessments (FINOS Incubation ML2 commitment).
- [`known-advisories.md`](known-advisories.md) — every advisory the CVE scanners are told to accept,
  with the reason and the review date; [`trivy-ignore-third-party.yaml`](trivy-ignore-third-party.yaml)
  is the Trivy half of it, for third-party images only.

Policy and reporting live in [SECURITY.md](../SECURITY.md); machine-readable metadata in
[security-insights.yml](../security-insights.yml).
