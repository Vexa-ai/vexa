# conformance — Group-6 (O-API-1) behavioral conformance for the sealed gateway contracts

The schema-only gate (`contracts/api.v1/validate.mjs`, `contracts/ws.v1/validate.mjs`)
checks that the FROZEN goldens parse against the FROZEN schemas. This package adds the
**behavioral** layer: it drives the gateway's REAL request/response and `/ws` protocol
behavior and asserts every live response/frame conforms to the sealed component schema —
all OFFLINE (no docker, no real backend, no meetings).

Behavior is derived verbatim from `services/api-gateway/main.py`:
- the `forward_request` auth middleware (`x-api-key` → `/internal/validate`; fail-closed
  401; scope enforcement → 403) and the CORE proxy routes (main.py:450-831), and
- the `/ws` multiplex control loop + redis-pubsub fan-in (main.py:2165-2340).

## Layout (mirrors the v0.12 carve — see `identity/services/admin-api/`)

```
conformance/
  pyproject.toml                       # uv package; gate:python discovers it (pyproject + tests/)
  src/gateway_conformance/
    contracts.py                       # load sealed api.v1/ws.v1 schemas BY PATH; validate by component
    fake_meeting_api.py                # port-fake downstream replaying the api.v1 goldens + /internal/validate
    gateway_app.py                     # build_gateway(): the PRODUCTION gateway.create_app + injected fakes
    ws_harness.py                      # /ws fakes (FakeWebSocket/FakeRedis/FakeAuthorizer) driving gateway._run_multiplex
    obs.py                             # re-exports gateway.obs (the production trace emitter — one SSOT)
  tests/
    test_api_surface.py                # O-API-1 api.v1 — 10 CORE paths + auth-/scope-negative
    test_ws_protocol.py                # O-API-1 ws.v1 — subscribe→ack, forwarded data frames, Error frames
```

## What the evals assert

### `test_api_surface.py` (api.v1)
Drives all **10 CORE paths** (exactly the `(path, method)` set `api.v1/validate.mjs`
asserts; `test_core_paths_match_validate_mjs` proves no drift) through a `TestClient`
gateway → in-process fake meeting-api returning the frozen goldens. For each path:
- **authed** request (valid `x-api-key`) → 2xx **and** body conforms to its sealed
  `#/components/schemas/<Shape>` (`BotStatusResponse`, `MeetingListResponse`,
  `MeetingResponse`, `TranscriptionResponse`), validated BY PATH via `jsonschema`.
- **conformance-oracle is real (negative)**: `test_malformed_downstream_body_is_caught_by_conformance`
  makes the downstream return a body that VIOLATES the sealed component and asserts the same
  validator DETECTS it through the proxy — so the body-conformance leg can fail on a real defect,
  it isn't a golden re-asserting its own schema.
- **auth-negative**: no `x-api-key` → 401 `{"detail":"Missing API key"}` (fail-closed).
- present-but-invalid key → 401 `Invalid API key`; tx-only token on `/bots` → 403
  (scope-negative). Plus on-disk goldens conform + sealed identity is main 1.5.0.

### `test_ws_protocol.py` (ws.v1)
Replays the `/ws` protocol against the unit harness:
- `subscribe` → a `Subscribed` ack frame conforms to `#/$defs/Subscribed`.
- `unsubscribe` → an `Unsubscribed` ack conforms to `#/$defs/Unsubscribed` **AND the fan-in stops**
  (a payload published after unsubscribe is NOT forwarded) — the sealed frame proven as RUNTIME
  behavior, not just a static golden; unknown-meeting unsubscribe → `invalid_unsubscribe_payload`.
- a forwarded redis payload on `tc:…:mutable` / `bm:…:status` / `va:…:chat` →
  `TranscriptionSegment` / `BotStatus` / `ChatMessage` frame conforms (raw forward).
- malformed input → an `Error` frame conforms (`invalid_json`, `unknown_action`,
  `invalid_subscribe_payload`); missing `x-api-key` → `missing_api_key` Error + close 4401.
- on-disk ws.v1 goldens conform BY PATH.

## Fidelity — conformance drives the SHIPPED app (twin-risk retired)

The top finding of the eval audit was twin-risk: `gateway/services/` once held only this
`conformance/` package, and `gateway_app.py` / `ws_harness.py` were a **hand-port** of the
deployed `services/api-gateway/main.py`, so "conformance green" proved a *copy* conformed, not
the shipped surface. **That is now closed.** The production module
[`../gateway/`](../gateway/) (`gateway.create_app`) is the v0.12 carve of `main.py` and the
**single source** of the proxy + `/ws` multiplex logic. This harness drives it:

- `gateway_app.build_gateway()` constructs `gateway.create_app` injected with the port-fake
  downstream + a fake admin-api `Authorizer` — so every REST assertion runs against shipped code.
- `ws_harness.WSMultiplexHarness.run()` drives the production `gateway.app._run_multiplex`
  against `FakeWebSocket` / `FakeRedis` / `FakeAuthorizer` (the gateway's ports) — so every
  ws.v1 frame assertion runs against the shipped multiplex.
- `obs.py` re-exports the production trace emitter (`gateway.obs`); the tracing eval installs
  its sink on the SAME emitter `create_app` uses.

Import direction is one-way: **conformance (test) → gateway (prod)**; the gateway package
imports nothing here.

Still machine-guarded against drift: `test_core_paths_match_validate_mjs` binds the driven
CORE `(path, method)` set to the sealed `api.v1`; the negative body test proves the conformance
oracle can fail. The WS **error vocabulary** is now reconciled (it had drifted): `ws.v1`
`Error.error` is a formal `enum`, so the harness's emitted codes are machine-checked against the
contract instead of a freeform string. `unknown_action` (the multiplex `else` branch) was added;
the `authorization_service_error` / `authorization_call_failed` downstream-auth codes are kept
(they're real codes the deployed gateway emits on the `POST /ws/authorize-subscribe` failure path)
but flagged production-only — the synchronous `FakeAuthorizer` can't reach them offline, and a
golden pins each. That reconciliation rode a `lane:contract` reseal (editing a sealed contract
breaks `gate:contract-version` by design). Remaining follow-on (tracked separately): the deeper
item is carving the *actually-deployed* gateway into v0.12 so this harness drives shipped code
rather than the `gateway.create_app` hand-port — which is also what would let the offline harness
exercise the downstream-auth codes end-to-end.

## Run

```bash
cd v0.12/gateway/services/conformance && uv run pytest -q
# rides the lane gate:
cd v0.12 && node scripts/gates.mjs schema python
```

Autonomous: `jsonschema` (Draft 2020-12, matching the contracts), FastAPI `TestClient`,
and an in-process httpx `ASGITransport` — no network, no docker, no greenlet (no
SQLAlchemy-async here).
