# tests — the Group-1 backing-stack evals (gate:stack)

Autonomous testcontainers evals — ephemeral Postgres + Redis, no live bbb stack, no meetings.
`conftest.py` owns the PG/Redis fixtures and the skip-if-docker-absent guard (mirrors
`runtime/tests/test_docker_backend.py`). O-STACK-1 `test_stack_postgres.py`, O-STACK-2
`test_stack_redis.py`, O-STACK-3 `test_stack_admin_api.py`.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
