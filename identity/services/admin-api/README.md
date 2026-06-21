# admin-api — identity service: users · scoped tokens · /internal/validate + Group-1 stack evals

The v0.12 carve of the parent `services/admin-api`, plus the **Group-1 backing-stack evals**
(the `gate:stack` home — the structure Groups 2–8 follow).

## Layout (the v0.12 stack-eval pattern)

```
admin-api/
  pyproject.toml          # uv package; gate:python discovers it (pyproject + tests/)
  src/admin_api/
    schema/               # the v0.12 SQLAlchemy source-of-truth (identity + meeting tables)
      models.py           #   User, APIToken, Meeting, Transcription, MeetingSession
                          #   (recordings/media_files DROPPED — see MIGRATION-0001)
      sync.py             #   ensure_schema() — idempotent convergence (no alembic)
      MIGRATION-0001-drop-recordings.md
    token_scope.py        # vxa_<scope>_ token minting ({bot,tx,browser})
    app/
      db.py               # injectable async engine (point at testcontainers or prod)
      main.py             # create_app() — 3 auth tiers, /internal/validate (fail-closed)
  tests/                  # the backing-stack evals — gate:stack runs these
    conftest.py           #   testcontainers PG + Redis fixtures; skip-if-docker-absent
    test_stack_postgres.py    # O-STACK-1
    test_stack_redis.py       # O-STACK-2
    test_stack_admin_api.py   # O-STACK-3
```

## Evals (autonomous — testcontainers, no live bbb, no meetings)

- **O-STACK-1 (postgres)** — bring up PG, `ensure_schema`, assert table set + idempotency, FK
  integrity (orphan inserts rejected), CRUD golden round-trips (user→token; meeting→
  transcription→session), the `recordings`-table-is-dead verdict, and the real JSONB recording
  path.
- **O-STACK-2 (redis)** — each usage class from `services/redis.md`: stream XADD→XREADGROUP→XACK
  (transcription_segments), pub/sub PUBLISH→SUBSCRIBE (tc:meeting:*, bot_commands:*,
  meeting:*:status), list LPUSH→BRPOP (webhook_retry_queue), sorted-set ZADD→ZRANGEBYSCORE.
- **O-STACK-3 (admin-api)** — FastAPI TestClient against testcontainers-PG: create user → mint
  scoped token → `/internal/validate` (user_id+scopes+webhook config; HMAC secret required +
  fail-closed) → revoke → expired rejected → invalid scope 422 → admin-tier auth enforced.

## Run

```bash
cd identity/services/admin-api && uv run pytest -q      # all three (skips if docker absent)
cd ../../.. && pnpm gate:stack                          # the gate (discovers + runs)
```

Skips cleanly where docker is unavailable; PASSES where docker exists (local or `ssh bbb`).

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface
is its `index`/contract; it may depend only on what the dependency-rules allow._
