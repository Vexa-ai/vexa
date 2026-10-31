"""#1964 — the public ``GET /status`` and the ``X-Vexa-Maintenance`` header (gateway side).

A1/A2 against ``create_app`` + ``apply_maintenance_header`` with a fake store standing in for the
Redis key admin-api writes; T1 with a store that raises, hangs or returns garbage (a stopped
Redis); T4 proves the default null store changes nothing about the replies' headers.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis
from gateway import create_app
from gateway.edge_guard import apply_guard
from gateway.maintenance import (MaintenanceCache, NullMaintenanceStore, READ_TIMEOUT_SECONDS,
                                 TTL_SECONDS, apply_maintenance_header)
from gateway.ports import MaintenanceStore

RECORD = {"id": "m-2026-11-01", "kind": "update", "message": "Upgrading", "components": ["bots"],
          "started_at": "2026-11-01T00:00:00Z", "expected_end": None}
HEADER = "x-vexa-maintenance"
#: (path, key, expected status) — the A1/A2 sample of public, authenticated and failing routes.
SAMPLE = [("/health", None, 200), ("/status", None, 200), ("/meetings", VALID_KEY, 200),
          ("/meetings", None, 401), ("/meetings", "not-a-key", 401), ("/no-such-route", None, 404)]


class FakeStore:
    """A ``MaintenanceStore`` that can answer, fail, hang, or block on a gate (single-flight)."""

    def __init__(self, raw=None, *, fail=False, hang=False):
        self.raw, self.fail, self.hang, self.calls, self.gate = raw, fail, hang, 0, None

    async def get(self):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise ConnectionError("redis down")
        return self.raw


def _app(store=None, *, header=True):
    app = create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis(), maintenance_store=store)
    if header:
        apply_maintenance_header(app)
    return app


def _sweep(client):
    """Every SAMPLE reply answers as before; returns each reply's maintenance header (or None)."""
    seen = {}
    for path, key, expected in SAMPLE:
        r = client.get(path, headers={"x-api-key": key} if key else {})
        assert r.status_code == expected, (path, key, r.status_code)
        seen[(path, key)] = r.headers.get(HEADER)
    return seen


# ── A1: no record ───────────────────────────────────────────────────────────────────────────────

def test_a1_no_record_is_operational_and_unstamped():
    client = TestClient(_app(FakeStore(None)))
    r = client.get("/status")
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=15"
    body = r.json()
    assert set(body) == {"status", "components", "maintenance", "updated_at"}
    assert body["status"] == "operational" and body["maintenance"] is None
    assert body["components"] == {"api": "operational", "transcription": "operational",
                                  "dashboard": "operational",
                                  "bots": {"google_meet": "operational", "zoom": "operational",
                                           "teams": "operational"}}
    assert set(_sweep(client).values()) == {None}


def test_status_needs_no_key_and_uses_the_null_store_by_default():
    assert isinstance(NullMaintenanceStore(), MaintenanceStore)
    r = TestClient(create_app(FakeAuthorizer(), FakeDownstream(), FakeRedis())).get("/status")
    assert r.status_code == 200 and r.json()["status"] == "operational"


# ── A2: an active record ────────────────────────────────────────────────────────────────────────

def test_a2_active_record_shows_in_status_and_stamps_every_reply():
    client = TestClient(_app(FakeStore(json.dumps(RECORD))))
    body = client.get("/status").json()
    assert body["status"] == "maintenance"
    assert body["maintenance"] == {"id": "m-2026-11-01", "kind": "update", "message": "Upgrading",
                                   "started_at": "2026-11-01T00:00:00Z", "expected_end": None}
    assert body["components"]["bots"] == {"google_meet": "maintenance", "zoom": "maintenance",
                                          "teams": "maintenance"}
    assert body["components"]["api"] == "operational"
    assert set(_sweep(client).values()) == {"m-2026-11-01"}


@pytest.mark.parametrize("components,expected", [
    ([], ["api", "bots.google_meet", "bots.zoom", "bots.teams", "transcription", "dashboard"]),
    (["bots.zoom", "api"], ["api", "bots.zoom"]),
    (["transcription", "nonsense"], ["transcription"]),
])
def test_named_components_read_maintenance_and_the_rest_operational(components, expected):
    body = TestClient(_app(FakeStore(json.dumps({**RECORD, "components": components})))).get("/status").json()
    c = body["components"]
    flat = {"api": c["api"], "transcription": c["transcription"], "dashboard": c["dashboard"],
            **{f"bots.{p}": v for p, v in c["bots"].items()}}
    assert sorted(k for k, v in flat.items() if v == "maintenance") == sorted(expected)
    assert all(v == "operational" for k, v in flat.items() if k not in expected)


async def test_the_guards_own_429_carries_the_header():
    """The middleware is outermost: a reply the guard builds (never reaching the router) is stamped."""
    app = _app(FakeStore(json.dumps(RECORD)), header=False)
    from test_edge_guard import _enforcing_config
    apply_guard(app, config=_enforcing_config(rate_limit=1, trusted_proxies=["127.0.0.1"]))
    apply_maintenance_header(app)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        xff = {"X-Forwarded-For": "10.0.0.77"}
        assert (await ac.get("/bots", headers=xff)).status_code == 401
        r = await ac.get("/bots", headers=xff)
    assert r.status_code == 429 and r.headers[HEADER] == "m-2026-11-01"


# ── T3 / A5: /status is excluded from the per-IP guard the way /health is ──────────────────────

async def test_t3_status_flood_passes_the_guard_while_a_guarded_path_429s():
    """At a tiny RPM, repeated GET /status from one IP keep answering 200 while /bots from the same
    IP trips the per-IP 429. Unique XFF IP: guard's RateLimitManager is a process-wide singleton."""
    from gateway.edge_guard import _GUARD_EXCLUDE_PATHS, build_guard_config
    from test_edge_guard import _enforcing_config
    assert "/status" in _GUARD_EXCLUDE_PATHS and "/status" in build_guard_config().exclude_paths
    app = _app(None)
    apply_guard(app, config=_enforcing_config(rate_limit=2, trusted_proxies=["127.0.0.1"],
                                              exclude_paths=_GUARD_EXCLUDE_PATHS))
    xff = {"X-Forwarded-For": "10.0.0.64"}
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for _ in range(10):
            assert (await ac.get("/status", headers=xff)).status_code == 200
        for _ in range(2):
            assert (await ac.get("/bots", headers=xff)).status_code == 401
        assert (await ac.get("/bots", headers=xff)).status_code == 429
        assert (await ac.get("/status", headers=xff)).status_code == 200


# ── T1: Redis unreachable ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("store", [FakeStore(fail=True), FakeStore("{not json"),
                                   FakeStore(json.dumps({"id": "bad id!", "kind": "update"}))])
def test_t1_store_failure_is_degraded_and_nothing_else_changes(store):
    client = TestClient(_app(store))
    body = client.get("/status").json()
    assert body["status"] == "degraded" and body["maintenance"] is None
    assert body["components"]["api"] == "degraded"
    assert body["components"]["bots"]["zoom"] == "operational"
    assert set(_sweep(client).values()) == {None}


async def test_t1_a_hanging_store_is_bounded_at_the_read_timeout():
    cache = MaintenanceCache(FakeStore(hang=True))
    t0 = time.monotonic()
    assert await cache.snapshot() == ("unreachable", None)
    elapsed = time.monotonic() - t0
    assert READ_TIMEOUT_SECONDS <= elapsed < 1.0, elapsed


async def test_t1_the_read_is_single_flight_and_concurrent_requests_do_not_wait():
    store = FakeStore(json.dumps(RECORD))
    store.gate = asyncio.Event()
    cache = MaintenanceCache(store)
    first = asyncio.create_task(cache.snapshot())
    await asyncio.sleep(0)  # the first request is now inside the store read
    others = await asyncio.wait_for(asyncio.gather(*[cache.snapshot() for _ in range(4)]), 0.1)
    assert others == [("ok", None)] * 4  # the current snapshot, no waiting
    store.gate.set()
    assert (await first)[1]["id"] == "m-2026-11-01"
    assert store.calls == 1


async def test_t1_the_cache_refreshes_after_the_ttl():
    clock = {"t": 100.0}
    store = FakeStore(None)
    cache = MaintenanceCache(store, clock=lambda: clock["t"])
    assert await cache.snapshot() == ("ok", None) and store.calls == 1
    clock["t"] += TTL_SECONDS - 1
    assert await cache.snapshot() == ("ok", None) and store.calls == 1  # inside the TTL: no read
    store.raw = json.dumps(RECORD)
    clock["t"] += 1
    state, record = await cache.snapshot()
    assert (state, record["id"], store.calls) == ("ok", "m-2026-11-01", 2)
    store.fail = True
    clock["t"] += TTL_SECONDS
    assert await cache.snapshot() == ("unreachable", None)  # a failed refresh drops the record


# ── T4: the default null store changes nothing ─────────────────────────────────────────────────

def test_t4_null_store_leaves_every_header_set_unchanged():
    with_mw, without = TestClient(_app(None)), TestClient(_app(None, header=False))
    for path, key, _ in SAMPLE:
        h = {"x-api-key": key} if key else {}
        a, b = with_mw.get(path, headers=h), without.get(path, headers=h)
        assert a.status_code == b.status_code
        assert set(a.headers) == set(b.headers) and HEADER not in a.headers, path
