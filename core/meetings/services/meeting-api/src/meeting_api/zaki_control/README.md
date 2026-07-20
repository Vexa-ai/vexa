# `zaki_control` — the sealed `zaki-control.v1` server

The engine half of the Hub-BFF control plane: owner-scoped provisioning, capture lifecycle over the
existing bot-manager, status/stop, content-free erasure receipts, and HMAC-signed lifecycle/usage
callbacks back to the Hub.

The browser never reaches this router. The authenticated Hub BFF is the sole caller and the sole
credential holder; no response here carries an engine base URL, service token, webhook key, storage
key, database setting or meeting passcode.

## Front door

`build_router(...)` is the only supported entry point, and `ControlConfig.from_env()` is the only
supported way to build its configuration. Composition lives in `app.py`; nothing is mounted unless
`zaki_control_config is not None`, and `app.py` refuses to mount a partial set of collaborators.

```text
POST /api/zaki/control/v1/{userId}/ensure
POST /api/zaki/control/v1/{userId}/captures
GET  /api/zaki/control/v1/{userId}/captures/{captureId}
POST /api/zaki/control/v1/{userId}/captures/{captureId}/stop
POST /api/zaki/control/v1/{userId}/meetings/{meetingId}/erase
POST /api/zaki/control/v1/{userId}/erase
GET  /api/zaki/control/v1/ready          # mounted by app.py, not by this router
```

## Module map

| File | Responsibility |
|---|---|
| `router.py` | HTTP seam: binding, idempotency claim/replay, the sealed URL predicate, route handlers |
| `callbacks.py` | Lifecycle adjacency graph, envelope construction, HMAC signing, the delivery outbox |
| `adapters.py` | Durable PostgreSQL control store |
| `fakes.py` | In-memory store for tests and the in-process app factory |
| `ports.py` | The `ControlStore` protocol and its value types |
| `schema.py` | Loads the sealed schema and validates every inbound body at the seam |

## Bounds and gates

| Bound | Value | Source |
|---|---|---|
| Operator gate | off unless `ZAKI_MINUTES_OPERATOR_ENABLED=true` | `ControlConfig` |
| Mount gate | off unless `ZAKI_MINUTES_CONTROL_ENABLED=true` | `ControlConfig.from_env` returns `None` |
| Signing secret | ≥ 32 chars, required when enabled | `ControlConfig.from_env` |
| Capture lifetime | 60 – 14400 s, default 3600 | `MINUTES_CONTROL_MAX_CAPTURE_SECONDS` |
| Capture platform | `google_meet` only | router; refused as `invalid_request` |
| Operator Jitsi hosts | validated configuration only, never ambient env | `ControlConfig.jitsi_hosts` |
| Callback replay window | 300 s | `callbacks.py` |

Enabling control without `ZAKI_MINUTES_TTL_ENABLED=true` is refused at boot: capture without a
running expiry worker would retain data past the advertised window.

## Invariants worth not breaking

- **Four-way identity.** Token scope, `{userId}` path, `X-Zaki-*` headers and `subject.*` must all
  agree. A mismatch is `subject_mismatch` and must not reveal whether a foreign resource exists.
- **Lifecycle adjacency is enforced, not assumed.** `_ADJACENCY` in `callbacks.py` is the single
  authority. The store's `UPDATE` is unconditional by design, so `record_capture_status` is what
  keeps a skipped join or a post-terminal move from being written. Recovery walks start from the
  capture's *current* state — replaying a fixed prefix would drive `stopping → joining`.
- **Closed error vocabularies.** Only `ErrorResponse.code` values may leave a control route. There
  is no free-form detail field, and every response is `Cache-Control: no-store`.
- **Receipts are content-free.** An erasure receipt is an ID, a timestamp and four non-negative
  counts. Never transcript text, attendee data, storage keys or credentials.
- **The contract is sealed.** `core/meetings/contracts/zaki-control.v1/` is read-only here. If the
  runtime and the seal disagree, the runtime is wrong.

## Tests

- `tests/test_zaki_control.py` — seam behaviour, crash recovery, fencing.
- `tests/test_zaki_control_conformance.py` — replays all sealed goldens through these primitives,
  plus the host-authority cases the corpus does not isolate on its own.

```bash
cd core/meetings/services/meeting-api && uv run pytest -q
node core/meetings/contracts/zaki-control.v1/validate.mjs
```
