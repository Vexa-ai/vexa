"""gate:health — the transcription-collector exposes a conforming liveness /health.

The collector's liveness probe: no auth (mirrors a real load-balancer health check), no store
call. 200 + {status:"ok", service:"transcription-collector"} = the process is up.
"""
from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore


def _app():
    return create_app(InMemoryTranscriptStore(), redis=None)


def test_health_ok():
    client = TestClient(_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "transcription-collector"


def test_health_needs_no_user_identity():
    """Health must be reachable WITHOUT an x-user-id — it is not a client route."""
    client = TestClient(_app())
    assert client.get("/health").status_code == 200


# ──────────────────────────────────────────────────────────────────────────────────────────────
# #636 · C2/A4 — the UNIFIED meeting-api /health surfaces the collector-group PEL depth, so a
# crashed replica's orphaned (delivered-but-un-acked) batch is a reportable state, not a silent
# stall. Drives meeting_api.create_app (the app that carries the #527 `pipeline` section), with the
# pipeline redis wired to a fake exposing xinfo_groups + a bounded XPENDING summary.
# ──────────────────────────────────────────────────────────────────────────────────────────────


class _PendingRedis:
    """A pipeline-redis fake: xinfo_groups (lag) + xpending SUMMARY (delivered-but-un-acked total)."""

    def __init__(self, *, pending, lag=0):
        self._pending, self._lag = pending, lag

    async def xinfo_groups(self, stream):
        return [{"name": "collector_group", "lag": self._lag}]

    async def xpending(self, stream, group):
        return {"pending": self._pending, "min": "1-0", "max": "9-0", "consumers": []}


def _unified_health(*, pending, lag=0, pending_alarm=0):
    import time

    from meeting_api import create_app as create_unified_app

    app = create_unified_app()
    app.state.pipeline_ticks = {"segment-consumer": time.monotonic(), "db-writer": time.monotonic()}
    app.state.pipeline_redis = _PendingRedis(pending=pending, lag=lag)
    app.state.pipeline_stream = "transcription_segments"
    app.state.pipeline_group = "collector_group"
    app.state.pipeline_tick_stale_s = 120.0
    app.state.pipeline_lag_alarm = 500
    app.state.pipeline_pending_alarm = pending_alarm
    return TestClient(app).get("/health")


def test_pending_depth_degrades():
    """Orphan present: the group PEL total is N=5 above the alarm → /health reports
    ``pipeline.pending_depth == 5``, ``status: degraded``, HTTP 503.

    RED on head: /health has only ``consumer_lag`` (= 0 for a delivered-but-un-acked orphan), so it
    stays ``ok`` / 200 and the stall is invisible."""
    r = _unified_health(pending=5, pending_alarm=0)
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "degraded"
    assert body["pipeline"]["pending_depth"] == 5


def test_pending_depth_zero_is_ok():
    """No orphan: PEL total 0 (steady state — entries ack within a tick) → ``pending_depth == 0``,
    ``status: ok``, 200."""
    r = _unified_health(pending=0, pending_alarm=0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["pipeline"]["pending_depth"] == 0
