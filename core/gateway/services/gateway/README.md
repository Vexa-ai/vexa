# gateway — the PRODUCTION edge (auth · routing · WS fan-out)

The v0.12 carve of the deployed `services/api-gateway/main.py`, as an injectable package.
This is the **single source** of the gateway lane's proxy + `/ws` multiplex logic. The
O-API-1 conformance suite (`../conformance/`) imports `create_app` from here and injects
in-process fakes, so "conformance green" now proves the **shipped** surface conforms — not a
hand-ported twin.

## Layout (mirrors the v0.12 carve — see `identity/services/admin-api/`)

```
gateway/
  pyproject.toml              # uv package; gate:python + gate:health discover it
  src/gateway/
    __init__.py              # the front door: create_app, the ports, ROUTE_SCOPES
    ports.py                 # Protocols: Authorizer, DownstreamClient, RedisBus (+ PubSub)
    app.py                   # create_app(authorizer, downstream, redis, ...) — REST proxy,
                             #   /ws multiplex, /health; the carve of main.py behavior
    adapters.py              # real adapters: httpx DownstreamClient, admin-api Authorizer,
                             #   redis bus + build_production_app() (the prod entrypoint)
    obs.py                   # the lane's logevent.v1 trace emitter (TraceMiddleware, log_event)
  tests/                     # gate:python + gate:health run these (injected fakes; conftest.py)
    test_health.py           #   /health → 200 {status:"ok", service:"gateway"}
    test_proxy.py            #   fail-closed auth, scope 403, verbatim passthrough, header inject
    test_multiplex.py        #   /ws subscribe→ack→forward, unsubscribe stops fan-in, ping/errors
```

## Ports (the seam)

`create_app` depends on BEHAVIOR via three `typing.Protocol`s, not concrete clients:

| Port               | prod adapter (`adapters.py`)            | conformance fake                         |
|--------------------|-----------------------------------------|------------------------------------------|
| `Authorizer`       | `AdminApiAuthorizer` (admin-api `/internal/validate` + tc `/ws/authorize-subscribe`) | fake admin-api / `FakeAuthorizer` |
| `DownstreamClient` | `HttpxDownstreamClient` (httpx forward) | port-fake meeting-api (ASGI transport)   |
| `RedisBus`         | `redis.asyncio` pub/sub                 | `FakeRedis` in-process pub/sub           |

Both the real adapters and the fakes satisfy the Protocols structurally — no inheritance.

## Behavior carved from `services/api-gateway/main.py`

- **auth (fail-closed)** — `x-api-key` resolved via `Authorizer`; missing/invalid → 401; a
  token lacking the route's `ROUTE_SCOPES` scope → 403 (main.py:287-369, 59-65).
- **CORE proxy routes** — each forwards its method to the matching downstream URL and returns
  the downstream body + status **verbatim** (main.py:450-831, 367). Identity headers injected;
  client-supplied identity headers stripped (anti-spoofing, main.py:294-296).
- **`/ws` multiplex** — subscribe → `Subscribed` ack; **unsubscribe → `Unsubscribed` ack +
  STOP fan-in**; ping → pong; `invalid_json` / `unknown_action` / `invalid_subscribe_payload`
  / `invalid_unsubscribe_payload` / `missing_api_key` errors; raw redis payloads forwarded
  over `tc:…:mutable` / `bm:…:status` / `va:…:chat` (main.py:2165-2340).
- **`/health`** — liveness `{status:"ok", service:"gateway"}` (gate:health).
- **tracing** — `TraceMiddleware` mints/reads `X-Trace-Id`, forwards it downstream; user/system
  `log_event`s on the auth + proxy spans conform to `logevent.v1` (gate:tracing).

## Import direction

One-way: **conformance → gateway**. This package imports nothing from `conformance/`.

## Run

```bash
cd v0.12/gateway/services/gateway && uv run pytest -q
# rides the lane gates:
cd v0.12 && node scripts/gates.mjs python health
```
