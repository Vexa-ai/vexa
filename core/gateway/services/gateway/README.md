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
`tool.v1`). See [`docs/docs/architecture/control-plane.mdx`](../../../../docs/docs/architecture/control-plane.mdx).

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| **calls** | `admin-api` | HTTP `POST /internal/validate` | `x-api-key` token → `{user_id, scopes, max_concurrent, webhook_*}` (fail-closed 401) |
| **calls** | `meeting-api` | HTTP proxy `/bots · /meetings · /transcripts · /recordings` | client request + injected `x-user-id`/`x-user-scopes`/`x-user-limits`, signed as `X-Vexa-Identity` with the Ed25519 key in `VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE` (gateway-identity.v1); body + status returned verbatim |
| **calls** | `meeting-api` | HTTP `POST /ws/authorize-subscribe` | `/ws` subscribe authorization → `{authorized[], errors[]}` — at subscribe, and again every 15 s for each open socket's subscriptions (`subscription_revoked` on a refusal) |
| **calls** | `admin-api` | HTTP `POST /internal/validate`, every 15 s per open socket | the socket's key RE-RESOLVED: a key that no longer resolves closes the socket (`4401`); a changed membership re-derives its workspace status channels |
| **calls** | `meeting-api` | WS `/bot-accounts/sessions/{session_id}/vnc` (`src/gateway/vnc_relay.py`) | a bot-account sign-in session's live view (ADR-0049): the client's frames relayed verbatim both ways, opened with the same signed identity headers an HTTP forward stamps |
| **consumes** | `redis` (producers: meeting-api + collector) | sub `tc:meeting:{id}:mutable` · `bm:meeting:{id}:status` · `va:meeting:{id}:chat` | raw transcript / status / chat payloads, forwarded unchanged to the socket |
| **consumes** | `redis` stream (producer: signals) | read `sig:meeting:{id}` (only for a subscription that named `streams:["signals"]`, for the row the authorize hop granted) | transcript-signal annotations, sent as `meeting.signals` frames |
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

## Route table and policy
Assembled at boot from each deployed domain's `routes.v1.json` (`src/gateway/routes_manifest.py`);
the edge declares only its own `/health` and `/auth/me`. A row carries its scopes and, with
`"delegation": true`, admits a worker's own delegation token (the MCP front door and the agent's
friction report; `src/gateway/delegation.py`); with `"mcp_reentry": true` it admits that token
only on the MCP's re-entry — the rows the MCP's tools call back into, and no others. A domain
fronted wholesale (the agent) declares `forward` — `{"edge_prefix": "/agent/", "upstream_prefix":
"/api/"}` — and the edge registers its rows from the manifest without naming any of them: the
catch-all, and a route of its own for every other row (literal, or with whole-segment `{name}`
parameters re-encoded like any path parameter). A meetings row is forwarded to its own path on
meeting-api, or to the `"upstream"` it names (`/user/webhook/deliveries` is meeting-api's
`/webhooks/deliveries`); the three meeting-bundle rows (`GET`/`POST /meetings/{meeting_id}/export`,
`POST /meetings/import`) are relayed as streams in both directions under their own deadline
(`BUNDLE_TIMEOUT`), every other meetings row through the buffered forward; every `{name}` in that target is filled under the same rule as a forwarded
row (`paths.forwarded_param`), and `{platform}` must be an api.v1 `Platform`. meeting-api reads the
same rows to check, on the route a request matched, the scopes this edge checked
(`meeting_api/route_scopes.py`). `mcp_reentry` is refused on a `{path:path}` catch-all. A meetings
row may end in one (the sign-in session's live-view files, `/bot-accounts/sessions/{session_id}/view/{path:path}`):
its tail is forwarded under the catch-all rule (`paths.tail_path`). The live view's websocket is not a
row — routes.v1 declares HTTP verbs — and is held to the scopes of `GET /bot-accounts/sessions/{session_id}`.

## The validate hop when admin-api is unavailable
**How it works.** `AdminApiAuthorizer.resolve` (`src/gateway/adapters.py`) POSTs each credential to
admin-api `/internal/validate` with a 5 s budget. A hop that could not connect (`ConnectError`,
`ConnectTimeout`) never reached admin-api, so it is sent again after 0.5 s and then 1.5 s
(`VALIDATE_CONNECT_RETRY_BACKOFF`): an admin-api restart costs a request about 2 s instead of a 503.
Any other failure is answered at once: a read timeout means admin-api has the request and is slow,
and a pool timeout is this edge's own saturation, so neither is repeated. After the last retry, or
on any of those, the edge answers 503, never 401: no verdict was reached on the key.

**Why it complies.** Only a request admin-api never received is repeated, so no verdict is asked for
twice and a slow identity service gets no extra load. The edge stays fail-closed: no path admits a
credential without a 200 from `/internal/validate`. Tests: `tests/test_adapters_resolve.py`
(`test_a_connect_failure_that_recovers_resolves_the_key`,
`test_a_persistent_connect_failure_still_answers_unavailable`,
`test_a_hop_that_reached_admin_api_or_hit_this_edges_pool_is_not_retried`).

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
- ✅ delivered — bot accounts (ADR-0049): the `/bot-accounts` rows forwarded to meeting-api, and the sign-in session's live-view websocket relayed (key resolved as `/ws`; 4401 / 4403 / 4404 refusals)
- ⬜ planned — add a user scope to `/ws` (auto-subscribe `u:{user_id}:*` on auth) forwarding `meetings.changed` / `workspace.committed` / `routine.status`
