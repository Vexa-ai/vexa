# execution-targets.v1 / golden

The spec, as example vectors (P8 — the goldens are the truth). `validate.mjs` checks each against
`execution-targets.schema.json` (`gate:schema`).

- `registry-minimal.json` — the smallest valid registry (one local target, no resources).
- `registry-full.json` — a rich instance: bbb + ci + a provisioned throwaway target, with resource refs.
