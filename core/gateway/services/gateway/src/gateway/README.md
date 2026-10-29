# gateway (package) — create_app · ports · adapters · obs

The production edge logic, injectable. Modules:

- **`__init__.py`** — the front door: `create_app`, the ports (`Authorizer`,
  `DownstreamClient`, `RedisBus`, `PubSub`), `ROUTE_SCOPES`.
- **`ports.py`** — the `typing.Protocol` seams. The app depends on these, not concrete
  clients, so the same `create_app` runs with real adapters in prod and injected fakes in tests.
- **`app.py`** — `create_app(authorizer, downstream, redis, ...)`: the REST proxy (fail-closed
  auth, scope 403, verbatim body passthrough on the CORE routes), the `/ws` mount, and
  `/health`. Behavior is the carve of `services/api-gateway/main.py` (cited inline).
- **`multiplex.py`** — `run_multiplex`, the `/ws` control loop (subscribe/unsubscribe/ping) and
  its redis fan-in. Every 15 s (`WS_REAUTH_INTERVAL_SEC`) each open socket RE-RESOLVES its key
  through identity (`POST /internal/validate`) — a key that no longer resolves is closed `4401`, and
  a changed workspace membership re-derives its `w:{id}:meetings` channels — and re-runs
  `/ws/authorize-subscribe` for what it streams, stopping any meeting that no longer passes with a
  ws.v1 `subscription_revoked` frame. Two failed hops in a row fail closed.
- **`delegation.py`** — where a worker's delegation token is admitted: `/mcp` and the MCP's own
  re-entry (`McpReentry`) on the routes declared `"mcp_reentry": true`, and what `/auth/me`
  reports about a worker's admin standing.
- **`paths.py`** — path parameters and catch-all tails, re-encoded as opaque segments or refused
  before any downstream hop.
- **`routes_manifest.py`** — assembles the route table from each deployed domain's `routes.v1.json`.
- **D3 at the edge** (`app.py` `_authorize`, `delegation.py` `shared_delegation`): a delegation
  whose turn other people read (or a workspace principal) is refused, with `shared_conversation`,
  on every routes.v1 row not declared `"shared": "allow"`, in every domain, before any forward.
  `routes_manifest.Assembly.shared_allow` holds the allowed rows.
- **`log_scrub.py`** — M7: a logging filter on the uvicorn loggers that redacts a credential query
  value (`api_key`, `token`, …) before any access or WebSocket line is formatted. `create_app`
  installs it. `/ws` still takes the `?api_key=` parameter, deprecated (`ws_auth.py`).
- **`ws_auth.py`** — where a websocket's key comes from: the `x-api-key` header, else a
  `vexa.key.<key>` subprotocol entry (a browser; the edge answers `vexa.v1`), else, deprecated until
  `changed.COMPAT_REMOVED_IN`, the `?api_key=` parameter (`/ws` logs `ws_query_key_deprecated` with a
  running count). The VNC relay refuses a key in the URL with `api_key_query_removed` and close `4401`
  (logged `ws_query_key_refused`). ws.v1's own subprotocol entries never travel upstream.
- **`changed.py`** — the refusal an old-style call gets (`{type, error, changed_in, do_instead, docs}`),
  the boot line (`config_key_removed`) for a configuration key the gateway no longer reads, and
  `COMPAT_REMOVED_IN` (0.13.6), the release that ends what `/ws` still does for 0.12 clients: the URL
  key, and subscribing without `tx` (`ws_scope_not_enforced`). Each use is counted in-process
  (`count_compat_use`, the count on every log line); the gateway has no metrics backend.
- **`identity_token.py`** — vendored `gateway-identity.v1`: signs the resolved identity onto every
  forward (byte-identical to the contract's canonical copy).
- **`ratelimit.py`** / **`edge_guard.py`** — the per-user request rate limit and the opt-in
  per-IP guard (HTTP middleware and the `/ws` pre-accept check).
- **`config_preflight.py`** — vendored `config.v1` boot preflight.
- **`__main__.py`** — `python -m gateway`, the production entrypoint.
- **`adapters.py`** — the real `httpx` + `redis` implementations of the ports, and
  `build_production_app(...)` (the prod entrypoint that wires them from env). Lazy-imports
  `httpx`/`redis` so the package imports cleanly in the test venv.
- **`obs.py`** — the lane's `logevent.v1` trace emitter: `TraceMiddleware` (mint/read/forward
  `X-Trace-Id`), `log_event` bound to `service="gateway"`, and the `make_*` factories the
  downstream conformance hop reuses for `service="meeting-api"`.

Import direction is one-way: conformance imports this package; this package imports no
conformance code.
