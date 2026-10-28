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


class FakeRevocationStore:
    """The service Redis as `/internal/validate` sees it: the live and revoked keys agent-api writes
    (app/delegation_revocation.py). Every token a test mints reads as recorded live by agent-api,
    unless the test `evict`s its live key or `revoke`s it. `down` makes every read fail like an
    unreachable Redis."""

    LIVE = "vexa:delegation:live:"

    def __init__(self):
        self.keys: set[str] = set()
        self.not_live: set[str] = set()
        self.down = False
        self.reads = 0

    def revoke(self, jti: str) -> None:
        from admin_api.app import delegation_revocation

        self.keys.add(delegation_revocation.REVOKED_PREFIX + jti)
        self.not_live.add(jti)

    def evict(self, jti: str) -> None:
        """The live key gone without a revocation: evicted, expired early, or never written."""
        self.not_live.add(jti)

    async def exists(self, key: str) -> int:
        self.reads += 1
        if self.down:
            raise ConnectionError("redis down")
        if key.startswith(self.LIVE):
            return int(key[len(self.LIVE):] not in self.not_live)
        return int(key in self.keys)


class FakeLinkLedger:
    """The service Redis as `/internal/signin-links/redeem` sees it (app/signin_links.py): SET NX
    with an expiry. `down` makes every write fail like an unreachable Redis."""

    def __init__(self):
        self.keys: dict[str, int] = {}
        self.down = False

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if self.down:
            raise ConnectionError("redis down")
        if nx and key in self.keys:
            return None
        self.keys[key] = ex
        return True

    # the shared rate counters (`/internal/rate/take`, PRD 0.13.4 M6)
    async def incr(self, key: str) -> int:
        if self.down:
            raise ConnectionError("redis down")
        self.counts = getattr(self, "counts", {})
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def decr(self, key: str) -> int:
        self.counts[key] -= 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.keys[key] = seconds
        return True


@pytest.fixture(autouse=True)
def link_ledger(monkeypatch):
    """Every test redeems sign-in links against an in-memory ledger, never a real Redis."""
    from admin_api.app import signin_links

    ledger = FakeLinkLedger()
    monkeypatch.setattr(signin_links, "_client", ledger)
    return ledger


@pytest.fixture(autouse=True)
def revocation_store(monkeypatch):
    """Every test answers delegation tokens against an empty in-memory store, never a real Redis; a
    test that needs a revoked token or a dead store asks for this fixture."""
    from admin_api.app import delegation_revocation

    store = FakeRevocationStore()
    monkeypatch.setattr(delegation_revocation, "_client", store)
    return store


class HashedTokens(dict):
    """A fake session's key rows, stored the way identity stores them: by the key's SHA-256. A test
    reads and writes them by the key's value, and the lookup identity makes (by hash) finds them."""

    def __init__(self, rows=None):
        super().__init__()
        for key, value in (rows or {}).items():
            self[key] = value

    @staticmethod
    def _k(key):
        from admin_api.key_store import hash_token  # noqa: PLC0415
        return key if isinstance(key, str) and len(key) == 64 and key.isalnum() and key == key.lower() \
            else hash_token(key)

    def __getitem__(self, key):
        return super().__getitem__(self._k(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._k(key), value)

    def __contains__(self, key):
        return super().__contains__(self._k(key))

    def get(self, key, default=None):
        return super().get(self._k(key), default)



@pytest.fixture(autouse=True)
def _at_rest_key(tmp_path_factory, monkeypatch):
    """Every deployment mounts the at-rest key (MIGRATION-0015, required at boot): so does every test.
    A test about a missing or replaced key deletes or replaces the variable itself."""
    import os
    import secrets as _secrets
    if not os.environ.get("VEXA_AT_REST_KEY_FILE"):
        f = tmp_path_factory.getbasetemp() / "at-rest.key"
        if not f.exists():
            f.write_text(_secrets.token_hex(32))
        monkeypatch.setenv("VEXA_AT_REST_KEY_FILE", str(f))
