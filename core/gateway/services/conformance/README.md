# gateway-conformance — the gateway lane behavioral harness (Python)

The **offline conformance harness** for the gateway edge: it drives the SHIPPED `gateway.create_app`
(REST) and `gateway.run_multiplex` (/ws) entirely in-process over a `TestClient` and port-fakes — no
docker, no network, no real backend — and validates every response/frame against the **sealed**
`api.v1` / `ws.v1` / `logevent.v1` schemas loaded BY PATH. Python because it rides `gate:python` and
imports the production gateway package directly (conformance → gateway, never the reverse).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| **calls** | `gateway` (prod app) | in-process `TestClient(build_gateway())` + `run_multiplex` harness | REST requests + /ws frames against the shipped edge |
| **spawns-over** | `meeting-api` (prod, unified) | injected downstream over in-process httpx `ASGITransport` | the one downstream hop (`/transcripts`, `/meetings`, `/ws-authorize`, folded-in collector) |
| **calls** | admin-api `/internal/validate` | `FakeAuthorizer` port-fake | api-key → auth/scope decisions (401 / 403 legs) |
| **consumes** | redis pub/sub | `FakeRedis` fan-in in `ws_harness.py` | transcription/bot/chat payloads forwarded as /ws frames |
| **consumes** | `gateway/contracts/{api,ws,logevent}.v1` | JSON Schema read **by path**, jsonschema-validated | the frozen surface every assertion checks against |

## Contracts

**Owns:** none — this is a test harness, it defines no `*.v1`.
**Consumes:** [`core/gateway/contracts/api.v1`](../../contracts/api.v1) (`#/components/schemas/<Shape>`),
[`core/gateway/contracts/ws.v1`](../../contracts/ws.v1) (`#/$defs/<Shape>`), and
[`core/gateway/contracts/logevent.v1`](../../contracts/logevent.v1) (`#/$defs/LogEvent`) — all read
off disk and tied to the sealed registry (`contracts.seal.json`). Schemas are never restated here.

## Isolated evaluation

Standalone in `tests/` (L1 contract + L3 in-process integration against the shipped apps):

```bash
uv run pytest -q        # uv manages this package's own venv/deps
```

- `test_health.py` — `gate:health` liveness leg.
- `test_api_surface.py` — api.v1: the 10 CORE paths + auth-negative (401/403) legs.
- `test_ws_protocol.py` — ws.v1: subscribe ack, forwarded redis frames, malformed → `Error`, close 4401.
- `test_gateway_seam.py` — hardened-edge regression guards (fail-closed auth, anti-spoofing, 502/504 fault mapping).
- `test_tracing.py` — O-OBS-1: one minted `trace_id` forwarded downstream; every line conforms to logevent.v1.

## Status

- ✅ delivered — api.v1 REST surface + auth/scope-negative conformance
- ✅ delivered — ws.v1 multiplex protocol conformance (acks, frames, malformed, close codes)
- ✅ delivered — `/health` liveness leg
- ✅ delivered — hardened-edge regression guards (auth fail-closed, anti-spoofing, upstream-fault mapping)
- ✅ delivered — O-OBS-1 tracing + logevent.v1 structured-log conformance
- ✅ delivered — drives SHIPPED `gateway` + unified `meeting-api` apps in-process (no docker)
