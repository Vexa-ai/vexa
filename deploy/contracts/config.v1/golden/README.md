# config.v1 goldens — conforming declaration vectors (the spec, P8)

`declaration-example.json` exercises every shape the schema carries: all three key classes
(required-explicit / defaulted / capability), both capability modes (`all` — the STT pair, where
some-but-not-all set ⇒ `misconfigured`; `any` — alternative credential paths), both live-probe
kinds (`http` — one authenticated call, unauthorized ⇒ misconfigured; `file` — a credentials file
with in-container mirror fallbacks), `secret` keys, narrowed and empty `targets`, and a
`surface_only` entry. Validated by `validate.mjs` (gate:schema).

The LIVE declarations are not goldens — they live next to each adopted service's code
(`config.v1.json`) and are validated against the same schema by `gate:config-contract` via
`validate.mjs --file`.
