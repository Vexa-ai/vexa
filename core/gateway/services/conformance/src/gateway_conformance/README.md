# gateway_conformance — the harness package

- `contracts.py` — loads `../contracts/api.v1/api.schema.json` + `ws.v1/ws.schema.json`
  BY PATH and validates a payload against a named component (`#/components/schemas/<X>`
  for api.v1, `#/$defs/<X>` for ws.v1) via a Draft-2020-12 validator with `$ref` resolution.
- `fake_meeting_api.py` — a FastAPI port-fake (meeting-api + transcription-collector +
  admin-api `/internal/validate`) replaying the api.v1 goldens.
- `gateway_app.py` — `build_gateway()`: constructs the PRODUCTION `gateway.create_app` (the
  shipped app) injected with the port-fake downstream + a fake admin-api `Authorizer`, both
  reached over an in-process httpx `ASGITransport`. The auth / fail-closed-401 / scope-403 /
  proxy logic lives in `gateway.app`, not here.
- `ws_harness.py` — `FakeWebSocket`, `FakeRedis` (pub/sub fan-in) and `FakeAuthorizer`
  satisfying the gateway's ports; `WSMultiplexHarness.run()` drives the production
  `gateway.app._run_multiplex`.
- `obs.py` — re-exports the production trace emitter `gateway.obs` (one SSOT).

Import direction: conformance → gateway (prod). The gateway package imports nothing here.
