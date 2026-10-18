# delegation.v1 goldens

Every token here is made with `secret: "delegation.v1-published-test-vector-key"`, published in this
repository, so it authenticates nothing. The goldens were minted by `../delegation.py`.

- `Claims.<case>.json` — a decoded payload: `human` (`workspaces: "*"`), `autonomous` (an explicit,
  sorted isolation set) and `targeted` (a human chat with a `target` workspace).
- `Vector.<case>.json` — the same three as minting vectors: the inputs, the header, the claims and
  the exact token. `validate.mjs` re-mints each in Node and must reproduce the token byte for byte.
- `Refusal.<case>.json` — tokens a verifier must refuse with `reason`: signed with another key, a
  payload changed after signing, another audience, verified at `exp`, a `jti` on the denylist, a
  token with no signature part, and a bearer of another scheme.
