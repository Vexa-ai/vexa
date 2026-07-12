# identity.v1 goldens

Reference instances that pin the contract. Filename = `<Shape>.<case>.json`; the part before the
first dot is the `$def` (`ScopedToken`, `AccessDecision`) each must conform to. Validated by
`../validate.mjs` (gate:schema). Add a golden for every new case the shape must keep accepting.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
