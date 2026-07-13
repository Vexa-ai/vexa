# core/gateway — the world-facing EDGE (auth · routing · WS fan-out)

The single production edge of the control plane: it resolves the caller's `x-api-key`
(fail-closed), enforces per-route scopes, proxies the CORE REST surface to meeting-api
**verbatim**, and runs the `/ws` multiplex that fans per-meeting redis channels out to the
client over one socket. Python/FastAPI, hexagonal (collaborators injected as ports) so the
**shipped** `create_app` is the same code the O-API-1 conformance suite drives against fakes.

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| **produces** | the world (clients, dashboard) | `api.v1` (OpenAPI 3.1) | REST surface: `/bots`, `/transcripts`, `/meetings`, `/recordings`, `/auth/me`, `/health` |
| **produces** | the world | `ws.v1` (`/ws`) | subscribe/unsubscribe/ping ↔ `subscribed`/`unsubscribed`/`pong`/`error` + forwarded live frames |
| **calls** | identity / admin-api | HTTP `/internal/validate` + `/ws/authorize-subscribe` (via `Authorizer` port) | `x-api-key` → `{user_id, scopes, max_concurrent, webhook_*}`; per-meeting subscribe authz |
| **calls** | meeting-api | HTTP forward (via `DownstreamClient` port) | proxied method/path/body + injected `x-user-*` identity headers |
| **consumes** | redis (per meeting) | channels `tc:meeting:{id}:mutable` · `bm:meeting:{id}:status` · `va:meeting:{id}:chat` (via `RedisBus` port) | raw payloads forwarded verbatim to the subscribed socket |
| **produces** | observability sink | `logevent.v1` (stdout JSON) | one envelope per log line; `X-Trace-Id` minted at the edge, forwarded downstream |

## Contracts

**Owns:** [`core/gateway/contracts/api.v1`](contracts/api.v1/) · [`core/gateway/contracts/ws.v1`](contracts/ws.v1/) · [`core/gateway/contracts/logevent.v1`](contracts/logevent.v1/) — all sealed in `contracts.seal.json` (gate:contract-version freezes the bytes).
**Consumes:** identity's admin-api HTTP surface (`/internal/validate`, `/ws/authorize-subscribe`) and the meeting-api redis channels above — neither is a `*.v1` it owns.

## Isolated evaluation

`services/gateway/tests/` — injected fakes (`conftest.py`), no network:

```bash
cd core/gateway/services/gateway && uv run pytest -q
```

L2 unit / L3 in-process integration: `test_health`, `test_proxy` (fail-closed auth, scope 403, verbatim passthrough, header injection), `test_multiplex` (subscribe→ack→forward, unsubscribe stops fan-in, ping/errors), `test_ratelimit`. The L1 contract + conformance layer lives in `services/conformance/` (imports `create_app` from here).

## Status

- ✅ delivered — fail-closed `x-api-key` auth + `ROUTE_SCOPES` 403 enforcement
- ✅ delivered — CORE REST proxy to meeting-api (verbatim body/status, identity-header injection, 502/504 upstream mapping)
- ✅ delivered — `/auth/me` caller identity + `/health` liveness
- ✅ delivered — `/ws` multiplex: subscribe-authz, per-meeting redis fan-in, unsubscribe/ping, error vocabulary
- ✅ delivered — `logevent.v1` tracing (`TraceMiddleware`, `X-Trace-Id` forwarding) + per-user rate limit
- ✅ delivered — fastapi-guard edge layer (`edge_guard.py`): per-IP throttle + auto-ban at the edge (REST-scoped; optional `/ws` hook via `GUARD_WS_ENABLED`). Off-by-default for self-hosted (`GUARD_ENABLED=false` on deploy surfaces; code default ON for deployments that set no value). The `/ws` guard is in-memory per-process: it does NOT share ban/rate-limit state with the Redis-backed HTTP `SecurityMiddleware`, and under `uvicorn --workers N>1` each worker keeps an independent WS ban set. Default OFF; opt-in via `GUARD_WS_ENABLED=true`.
- ⬜ planned — user-scoped `/ws` (auto-subscribe to `u:{user_id}:*` on auth)
- ⬜ planned — new `ws.v1` frame `meetings.changed`
- ⬜ planned — new `ws.v1` frame `workspace.committed`
- ⬜ planned — new `ws.v1` frame `routine.status`
