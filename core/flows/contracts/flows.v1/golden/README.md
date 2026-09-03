# flows.v1 goldens

Reference instances that pin the contract. Filename = `<Shape>.<case>.json`; the part before the
first dot is the `$def` (`Carrier`, `CarrierRegistry`) each must conform to. Validated by
`../validate.mjs` (gate:schema), alongside the live census at `../carriers.json`.

`Carrier.onboarding-completed.json` is the entry a consumer builds against: the exact refs identity
promises, the stamp behind the exactly-once claim, and the id the fact is keyed by. Add a golden for
every new case the shape must keep accepting.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
