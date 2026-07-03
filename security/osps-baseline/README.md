# OSPS Baseline — self-assessments

Dated results of running the [OSPS Baseline](https://baseline.openssf.org/) scanner
([`ossf/pvtr-github-repo-scanner`](https://github.com/ossf/pvtr-github-repo-scanner)) against this
repository, kept as evidence for the FINOS Incubation commitment to
[OSPS Baseline Maturity Level 2](https://baseline.openssf.org/) (see
[finos/community#422](https://github.com/finos/community/issues/422)).

| Date | Scanner | Applicability | Result |
|---|---|---|---|
| 2026-07-02 | pvtr-github-repo-scanner v0.24.0 · catalog `osps-baseline-2026-02` | ML1 + ML2 | **28 passed · 0 failed · 1 warning** ([full results](2026-07-02-assessment.yaml)) |

The single warning is `OSPS-AC-04.01` ("GitHub Actions is disabled — manual review required"), a
scanner false-positive on this repository: Actions are enabled and the
[`gates` workflow](../../.github/workflows/gates.yml) runs on every push and pull request.

Result files are sanitized before commit: the scanner's raw `payload` section (scraped repository
data, which embeds the API token used for the scan) is stripped; the `evaluation-suites` assessment
results are kept verbatim.

Reproduce: see [Local Usage](https://github.com/ossf/pvtr-github-repo-scanner#local-usage) — point
`config.yml` at `Vexa-ai/vexa-core` with catalog `osps-baseline-2026-02`.
