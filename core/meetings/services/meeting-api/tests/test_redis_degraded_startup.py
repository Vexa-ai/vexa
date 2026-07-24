"""#809 — a Redis outage must not become a full core-API outage.

Incident (hosted prod, 2026-07-19 ~03:00-03:45 UTC): an LKE node recycle wedged the Redis RWO
volume for ~40 min. The prepared direction: meeting-api starts WITHOUT Redis, the shared /health
probe stays 200 (readiness true) so the DB-backed reads keep serving, /health reports Redis
HONESTLY as an unreachable component (the incident's /health read "ok" the whole time), and a
genuinely Redis-dependent write (DELETE /bots) fails NARROWLY (503) rather than as an opaque 500.

No live meeting, no server: a ``_DeadRedis`` raises ``ConnectionError`` on every command, exactly
as ``redis.asyncio``'s client does against an unreachable host. These drive ``create_app`` directly
(the SAME app the production composition root wires) so the assertions bind the shipped behaviour.

RED→GREEN pivots (the honest ones — the boot no longer crashes on this tree, so THAT row is a
standing regression lock, GREEN on base):
  * /health exposes ``pipeline.redis_reachable == false`` — RED on base (no such field; /health lied
    "ok"), GREEN after the app.py honesty fix. Negative control: the field is ``true`` when Redis is up.
  * DELETE /bots returns 503 (not 500) when the command bus is down — RED on base (uncaught
    ConnectionError → 500), GREEN after the stop_router narrow-fail.
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from meeting_api.app import create_app


class _DeadRedis:
    """Every command raises ConnectionError — an unreachable Redis, like redis.asyncio's real client."""

    def __init__(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        self._exc = RedisConnectionError("Error 61 connecting to redis:6379. Connect call failed.")

    async def ping(self, *a, **k):
        raise self._exc

    async def xinfo_groups(self, *a, **k):
        raise self._exc

    async def xpending(self, *a, **k):
        raise self._exc

    async def publish(self, *a, **k):
        raise self._exc


class _LiveRedis(_DeadRedis):
    """Reachable Redis: PING succeeds; stream probes return empty (no group yet) — the negative control."""

    async def ping(self, *a, **k):
        return True

    async def xinfo_groups(self, *a, **k):
        return []

    async def xpending(self, *a, **k):
        return {"pending": 0}


def _app_with_pipeline(redis_client):
    """The shared app with the #527 pipeline heartbeats wired (so /health runs _pipeline_health) and a
    fresh (already-stamped) db-writer tick, so the ONLY health signal under test is Redis reachability."""
    app = create_app()
    app.state.pipeline_ticks = {"db-writer": time.monotonic()}
    app.state.pipeline_redis = redis_client
    app.state.pipeline_stream = "transcription_segments"
    app.state.pipeline_group = "collector"
    app.state.pipeline_tick_stale_s = 120.0
    app.state.pipeline_lag_alarm = 500
    app.state.pipeline_pending_alarm = 100
    return app


def test_health_stays_200_and_reports_redis_unreachable_honestly():
    """Readiness stays TRUE (200) through a full Redis outage, and /health names Redis honestly.

    RED on base for the honesty half: base /health had no ``redis_reachable`` field and read status
    "ok" — indistinguishable from a healthy Redis (the incident's undiagnosable /health)."""
    app = _app_with_pipeline(_DeadRedis())
    with TestClient(app) as c:  # __enter__ runs lifespan → the app boots with Redis down
        r = c.get("/health")
    assert r.status_code == 200, "a Redis outage must NOT 503 the shared probe (readiness stays true)"
    body = r.json()
    # status is not degraded BY Redis alone — the DB-backed paths still serve.
    assert body["status"] == "ok"
    # The honest per-component signal the incident lacked.
    assert body["pipeline"]["redis_reachable"] is False


def test_health_reports_redis_reachable_when_up():
    """Negative control: a reachable Redis reports ``redis_reachable: true`` (green means real)."""
    app = _app_with_pipeline(_LiveRedis())
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["pipeline"]["redis_reachable"] is True


def test_stop_route_fails_narrowly_503_when_command_bus_down():
    """DELETE /bots is genuinely Redis-dependent (pub/sub is the only leave-command delivery). With
    Redis down it must fail NARROWLY (503, retryable) — RED on base (uncaught ConnectionError → 500)."""

    class _Repo:
        async def find_active(self, user_id, platform, native_meeting_id):
            return {"id": 42, "status": "active", "bot_container_id": None, "data": {}}

        async def list_sessions(self, meeting_id):
            return []

        async def update_meeting_status(self, **k):
            return None

    app = create_app(meeting_repo=_Repo(), command_publisher=_DeadRedis())
    with TestClient(app) as c:
        r = c.delete("/bots/google_meet/abc-defg-hij", headers={"x-user-id": "7"})
    assert r.status_code == 503, "a down command bus must fail narrowly per-request, not 500 process-wide"
