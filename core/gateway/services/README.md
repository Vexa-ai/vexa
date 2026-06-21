# gateway/services — the gateway lane's runnable packages

Houses the gateway lane's local packages:

- **`gateway/`** — the PRODUCTION edge: `gateway.create_app(authorizer, downstream, redis)`,
  the v0.12 carve of `services/api-gateway/main.py` (REST proxy, `/ws` multiplex, `/health`).
  Collaborators are injected as ports so the same app runs with real adapters in prod and
  injected fakes in tests.
- **`conformance/`** — the Group-6 behavioral-conformance harness for the sealed `api.v1` +
  `ws.v1` contracts. It imports `create_app` from `gateway/` and injects fakes, so every
  O-API-1 assertion drives the **shipped** app (not a hand-ported twin).

Import direction: conformance → gateway. The gateway package imports nothing from conformance.
