# logevent.v1 goldens

Example log lines — the spec by example (P8). Filename prefix → `audience`/intent class;
every file validates against `#/$defs/LogEvent` (see `../validate.mjs`):

- `user-*` — a user-facing event (`audience=user`).
- `system-*` — a system/debug event (`audience=system`).
- `error-*` — an error-level system event (`audience=system`, `level=error|critical`).

All three carry the **same `trace_id`** to show one request traced across hops.
