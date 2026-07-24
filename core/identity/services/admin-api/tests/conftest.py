"""Shared testcontainers fixtures for the Group-1 backing-stack evals.

Autonomous + CI-runnable: ephemeral Postgres + Redis via docker (testcontainers). NO live bbb
stack, NO meetings. Skip-if-docker-unavailable mirrors `runtime/tests/test_docker_backend.py`
(`_docker_ok` + module-level skipif) — the harness PASSES where docker exists, SKIPS where it
does not.
"""
import shutil
import subprocess

import pytest


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


requires_docker = pytest.mark.skipif(not _docker_ok(), reason="docker daemon not available")


@pytest.fixture(scope="session")
def pg_url():
    """Ephemeral Postgres → sync psycopg URL. Session-scoped (one container for the suite)."""
    if not _docker_ok():
        pytest.skip("docker daemon not available")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def pg_async_url(pg_url):
    """Same Postgres, asyncpg driver — for the FastAPI app (async engine)."""
    # testcontainers hands back postgresql+psycopg://...; swap the driver for asyncpg.
    return pg_url.replace("postgresql+psycopg://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def redis_client():
    """Ephemeral Valkey → a connected redis-py client. Session-scoped.

    Valkey (BSD-3 fork of Redis 7.2.4) is the engine we ship on every surface (#653); the eval runs
    against it, not stock Redis, so the stack it witnesses matches what users deploy. RedisContainer
    waits via a redis-py TCP PING (RESP), not an in-container `redis-cli`, so the Valkey image — which
    ships `valkey-cli`, not `redis-cli` — is a clean drop-in.
    """
    if not _docker_ok():
        pytest.skip("docker daemon not available")
    import redis as redis_lib
    from testcontainers.redis import RedisContainer

    with RedisContainer("valkey/valkey:8-alpine") as rc:
        host = rc.get_container_host_ip()
        port = int(rc.get_exposed_port(6379))
        client = redis_lib.Redis(host=host, port=port, decode_responses=True)
        client.ping()
        yield client
        client.close()
