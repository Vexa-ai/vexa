# scripts — repo tooling — the gate suite (gates.mjs) and helpers

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._

- `vexum-backport.mjs` — list, check, and backport signed Vexum commits using `vexum-paths.txt`; offline coverage in `vexum-backport.test.mjs`.
