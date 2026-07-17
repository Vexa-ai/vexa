"""#527 · C1/A1 — /health exposes pipeline liveness so a dead consumer no longer looks alive.

On 2026-04-26 the persistence consumer froze inside one await for 10h38m: /health kept returning
200, the k8s probe kept passing, and live WS kept flowing (a separate path) — while zero rows
reached the DB and 20 meetings' transcripts were lost. These drive the UNIFIED app (meeting_api.
create_app) and inject the same app.state the background loops set, so the /health decision is
proven offline without redis. (tests/test_health.py covers lifecycle.receiver.create_app — a
DIFFERENT app — so this is the only lane over the unified /health pipeline section.)
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from meeting_api import create_app


class _FakeRedis:
    def __init__(self, lag=0, ok=True):
        self._lag, self._ok = lag, ok

    async def xinfo_groups(self, stream):
        if not self._ok:
            raise RuntimeError("NOGROUP")
        return [{"name": "collector_group", "lag": self._lag}]


def _client(*, ticks, lag=0, ok=True, stale_s=120, lag_alarm=500):
    app = create_app()
    app.state.pipeline_ticks = ticks
    app.state.pipeline_redis = _FakeRedis(lag=lag, ok=ok)
    app.state.pipeline_stream = "transcription_segments"
    app.state.pipeline_group = "collector_group"
    app.state.pipeline_tick_stale_s = float(stale_s)
    app.state.pipeline_lag_alarm = int(lag_alarm)
    return TestClient(app)


def test_bare_app_omits_pipeline_section_and_stays_ok():
    """Regression guard: the app-factory path (no loops wired → no app.state.pipeline_ticks) keeps
    the pre-#527 /health verbatim — status ok, no pipeline section — so every existing consumer works."""
    r = TestClient(create_app()).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "pipeline" not in r.json()


def test_healthy_pipeline_is_ok_with_section():
    now = time.monotonic()
    r = _client(ticks={"segment-consumer": now, "db-writer": now}, lag=3).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.json()["pipeline"]["consumer_lag"] == 3
    assert set(r.json()["pipeline"]["loops"]) == {"segment-consumer", "db-writer"}


def test_stale_loop_degrades_503():
    """A loop whose heartbeat is older than the threshold (a hang) flips status→degraded + 503 — the
    signal that was missing on 2026-04-26."""
    r = _client(ticks={"segment-consumer": time.monotonic() - 9999,
                       "db-writer": time.monotonic()}, stale_s=120).get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert r.json()["pipeline"]["loops"]["segment-consumer"] > 120


def test_high_consumer_lag_degrades_503():
    r = _client(ticks={"segment-consumer": time.monotonic()}, lag=10_000, lag_alarm=500).get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"


def test_lag_probe_failure_reports_unavailable_not_a_hang():
    """The probe itself must never hang: a dead/absent group → consumer_lag 'unavailable', 200 (the
    loops are fresh) — observability, never a blocked request."""
    r = _client(ticks={"segment-consumer": time.monotonic()}, ok=False).get("/health")
    assert r.status_code == 200
    assert r.json()["pipeline"]["consumer_lag"] == "unavailable"
