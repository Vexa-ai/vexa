# identity/contracts — sealed identity wire shapes

Versioned JSON Schema contracts the identity lane publishes: `identity.v1` — the scoped token +
access-decision shapes; `signin.v1` — sign-in admission and the admin claim between the terminal's
server and admin-api, whose reason vocabularies are generated into both sides (`signin.v1/gen.mjs`). A `.vN` dir is frozen once sealed in `contracts.seal.json`
(gate:contract-version); validate with `node identity.v1/validate.mjs` (gate:schema).

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
