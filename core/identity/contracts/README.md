# identity/contracts — sealed identity wire shapes

Versioned JSON Schema contracts the identity lane publishes. Today: `identity.v1` — the scoped
token + access-decision shapes. A `.vN` dir is frozen once sealed in `contracts.seal.json`
(gate:contract-version); validate with `node identity.v1/validate.mjs` (gate:schema).

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
