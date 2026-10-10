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
  its redis fan-in.
- **`delegation.py`** — where a worker's delegation token is admitted: `/mcp` and the MCP's own
  re-entry (`McpReentry`) on the routes declared `"mcp_reentry": true`, and what `/auth/me`
  reports about a worker's admin standing.
- **`paths.py`** — path parameters and catch-all tails, re-encoded as opaque segments or refused
  before any downstream hop.
- **`routes_manifest.py`** — assembles the route table from each deployed domain's `routes.v1.json`.
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
