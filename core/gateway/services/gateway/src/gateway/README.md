# gateway (package) — create_app · ports · adapters · obs

The production edge logic, injectable. Modules:

- **`__init__.py`** — the front door: `create_app`, the ports (`Authorizer`,
  `DownstreamClient`, `RedisBus`, `PubSub`), `ROUTE_SCOPES`.
- **`ports.py`** — the `typing.Protocol` seams. The app depends on these, not concrete
  clients, so the same `create_app` runs with real adapters in prod and injected fakes in tests.
- **`app.py`** — `create_app(authorizer, downstream, redis, ...)`: the REST proxy (fail-closed
  auth, scope 403, verbatim body passthrough on the CORE routes), the `/ws` multiplex
  (`_run_multiplex`: subscribe/unsubscribe/ping + redis fan-in), and `/health`. Behavior is
  the carve of `services/api-gateway/main.py` (cited inline).
- **`adapters.py`** — the real `httpx` + `redis` implementations of the ports, and
  `build_production_app(...)` (the prod entrypoint that wires them from env). Lazy-imports
  `httpx`/`redis` so the package imports cleanly in the test venv.
- **`obs.py`** — the lane's `logevent.v1` trace emitter: `TraceMiddleware` (mint/read/forward
  `X-Trace-Id`), `log_event` bound to `service="gateway"`, and the `make_*` factories the
  downstream conformance hop reuses for `service="meeting-api"`.

Import direction is one-way: conformance imports this package; this package imports no
conformance code.
