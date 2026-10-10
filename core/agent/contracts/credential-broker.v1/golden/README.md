# credential-broker.v1 goldens

Wire-shape fixtures, one per `$def` and case. Filename `<Shape>.<case>.json`; the prefix names the
`$def` the file must conform to (validated by `../validate.mjs`, run by `gate:schema`).

`SignedAssertionVector.*.json` are deterministic signing vectors: a published test key, the claims,
the request body, and the exact `encoded`, `signature` and `header` they produce. The Python
binding (`../assertion.py` and its vendored copies), the TypeScript binding
(`clients/terminal/src/app/api/connections/assertion.ts`) and `validate.mjs` must each reproduce and
verify them. The keys are fixtures, never deployment keys: `assertion.py` lists them as
`PUBLISHED_KEYS`, `load_key` refuses a key file that holds one, and the broker refuses to boot with
one. Every other value is synthetic.
