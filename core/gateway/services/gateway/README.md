# gateway — the public edge (Python)

## Purpose
The single public front door for the v0.12 control plane: it authenticates every request at
the edge (`x-api-key` → admin-api `/internal/validate` → injected `x-user-id`/`x-user-scopes`),
proxies the REST surface verbatim to meeting-api, and serves the `/ws` multiplex that fans live
redis channels into one authenticated socket. It is the v0.12 carve of the deployed
`services/api-gateway/main.py`, with collaborators (admin-api, downstream, redis) injected as
ports so the same shipped `create_app` runs in prod and under the conformance harness.

## Boundary (SoC)
**This is the single edge.** It is about: authentication, *verbatim* proxy to the domain APIs, and
**composition** across domains (`/ws` fan-in, and cookbook ops that orchestrate ≥2 domain contracts).
**It is never about:** the business logic of either domain. The two domains (`meetings`, `agent`) never
talk directly — they meet **here**, over published contracts (`api.v1`, `ws.v1`, `transcript.v1`,
`tool.v1`). See [`docs/CONTROL-PLANE.md`](../../../../docs/CONTROL-PLANE.md).

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| **calls** | `admin-api` | HTTP `POST /internal/validate` | `x-api-key` token → `{user_id, scopes, max_concurrent, webhook_*}` (fail-closed 401) |
| **calls** | `meeting-api` | HTTP proxy `/bots · /meetings · /transcripts · /recordings` | client request + injected `x-user-id`/`x-user-scopes`/`x-user-limits`; body + status returned verbatim |
| **calls** | `meeting-api` | HTTP `POST /ws/authorize-subscribe` | `/ws` subscribe authorization → `{authorized[], errors[]}` |
| **consumes** | `redis` (producers: meeting-api + collector) | sub `tc:meeting:{id}:mutable` · `bm:meeting:{id}:status` · `va:meeting:{id}:chat` | raw transcript / status / chat payloads, forwarded unchanged to the socket |
| **produces** | clients (dashboard, SDKs) | WS `/ws` (`ws.v1`) | `subscribed`/`unsubscribed`/`pong`/`error` control + type-tagged live data frames |
| **produces** | clients | HTTP `/bots`, `/meetings`, `/transcripts`, `/recordings`, `/auth/me`, `/health` (`api.v1`) | the frozen public REST surface |
| **publishes** | log sink | stdout, one JSON line per log | `logevent.v1` envelopes (auth + proxy spans) carrying the `X-Trace-Id` |

## Contracts
**Owns:** [`api.v1`](../../contracts/api.v1) (frozen public REST/OpenAPI surface) ·
[`ws.v1`](../../contracts/ws.v1) (the `/ws` multiplex protocol) ·
[`logevent.v1`](../../contracts/logevent.v1) (structured log envelope + `trace_id`). All sealed in
`contracts.seal.json`.
**Consumes:** its own `api.v1`/`ws.v1` shapes by-path at the edge; admin-api's `/internal/validate`
and meeting-api's `/ws/authorize-subscribe` are HTTP hops, not `*.v1` contracts.

## Isolated evaluation
`tests/` holds unit evals (L2) over `create_app` with in-process fakes injected via `conftest.py`
(fake `Authorizer`, recording `DownstreamClient`, in-process `RedisBus`): `test_health`,
`test_proxy`, `test_multiplex`, `test_ratelimit`. The sealed-contract conformance (L1, every
frame/body validated against `api.v1`/`ws.v1`) lives in `../conformance/` and drives THIS package.
Run:

```bash
uv run pytest -q        # L2 unit; uv manages this package's own venv/deps
```

## Status
- ✅ delivered — edge auth (`x-api-key` → admin-api `/internal/validate`, fail-closed 401, scope 403, identity-header injection + spoof-strip)
- ✅ delivered — REST proxy to meeting-api (verbatim body+status; 502/504 on upstream fault)
- ✅ delivered — `/ws` multiplex (subscribe/unsubscribe/ping, downstream authorize, redis fan-in over `tc:`/`bm:`/`va:` channels)
- ✅ delivered — `/auth/me`, `/health`, per-user request rate limit (429), `logevent.v1` tracing
- ⬜ planned — add a user scope to `/ws` (auto-subscribe `u:{user_id}:*` on auth) forwarding `meetings.changed` / `workspace.committed` / `routine.status`
