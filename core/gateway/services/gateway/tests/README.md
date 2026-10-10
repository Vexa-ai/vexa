# gateway/tests — the production package's own unit evals

Focused unit tests proving `create_app` in isolation, with injected in-process fakes
(`conftest.py` — a fake admin-api `Authorizer`, a recording `DownstreamClient`, an in-process
`RedisBus`). The gateway package must NOT import the conformance harness, so these fakes live
here rather than being borrowed from `../conformance/`.

- **`test_health.py`** — gate:health: `/health` → 200 `{status:"ok", service:"gateway"}`,
  reachable without an api-key.
- **`test_proxy.py`** — fail-closed auth (no/bad key → 401), scope 403, verbatim body+status
  passthrough, identity-header injection + spoof-strip, route→downstream-base mapping.
- **`rfc8032.py`** — the RFC 8032 TEST 1 seed the gateway-identity.v1 signing vectors are made
  with. The goldens carry no signing key; `test_identity_token.py` derives it here.
- **`test_multiplex.py`** — `/ws`: missing key → close 4401; subscribe→ack→raw forward;
  unsubscribe→ack + fan-in STOPS; ping→pong; invalid_json / unknown_action errors.

The sealed-contract conformance (every frame/body validated against api.v1 / ws.v1 BY PATH)
lives in `../conformance/`, which drives THIS package's `create_app`. Run: `uv run pytest -q`.
