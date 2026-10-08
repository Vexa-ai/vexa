# gateway-identity.v1 goldens

- `claims-human.json` — the payload the gateway signs for an API key: subject, email, scopes,
  limits, a workspace membership and a webhook.
- `claims-delegated.json` — the payload for a worker's delegation token: the same person, with the
  dispatch's `delegation` ceiling (`autonomous`, one workspace, a target).
- `vector-human.json`, `vector-delegated.json` — signing vectors. Signing `claims` at `now` with
  `secret` (a test value, never a deployment's) must produce `token`; `validate.mjs` re-signs them
  in Node and `test_identity_token.py` in each vendoring package re-signs them in Python.
