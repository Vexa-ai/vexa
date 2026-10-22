"""08S-b, flows' side: a calendar read the broker refuses for want of the person's grant stops the
sensor with the one step that fixes it, instead of retrying or reading as a broken connection.

The broker's own tests (`credential-broker/tests/test_sensor_grants.py`) hold the refusal itself;
these hold what the sensor pool does with the broker's real answer on the wire.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flows import FakeClock  # noqa: E402
from flows_sensors import pool, store  # noqa: E402
from flows_sensors import calendar as cal  # noqa: E402
from flows_sensors.base import SensorFault  # noqa: E402
from sqlite_double import SqliteDB  # noqa: E402


@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    key = tmp_path / "sensors.key"
    key.write_text("car41-sensors-test-key-" + "0123456789abcdef" * 3)
    monkeypatch.setenv("VEXA_FLOWS_SENSORS_BROKER_URL", "http://broker.test")
    monkeypatch.setenv("VEXA_FLOWS_SENSORS_KEY_FILE", str(key))
    sent = []

    def answer(status, body):
        def urlopen(req, timeout=None):
            sent.append(json.loads(req.data))
            if status == 200:
                return io.BytesIO(json.dumps(body).encode())
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(json.dumps(body).encode()))
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return answer, sent


def test_no_grant_stops_the_sensor_with_the_step_that_fixes_it(broker_env):
    answer, _ = broker_env
    answer(409, {"detail": "This person has not let their sensors read this calendar",
                 "reason": "sensor_grant_required"})
    with pytest.raises(SensorFault) as e:
        cal.broker_call("7", cal.PATH, {"owner": "7"}, session="sensor:user:7/s")
    assert e.value.retryable is False
    assert e.value.reason.startswith(cal.GRANT_NEEDED)
    assert "calendar_sensor_grant" in e.value.reason

    db, clock = SqliteDB(), FakeClock(1_800_000_000)
    kinds = {"calendar": cal.CalendarSensor()}
    store.create(db, kinds, "user:7", name="s", kind="calendar")
    t = store.test(db, kinds, "user:7", "s")
    assert t["ok"] is False and t["reason"].startswith(cal.GRANT_NEEDED)
    # a sensor enabled before grants existed stops on its next read, saying why
    db.execute("UPDATE sensor SET status='active', tested_version=version, next_due_at=0")
    pool.run_due(db, kinds, lambda *a: 0, clock.now())
    h = pool.health(db, "user:7")[0]
    assert h["status"] == "stopped" and h["reason"].startswith(cal.GRANT_NEEDED)


def test_positive_control_a_granted_read_feeds_the_sensor_and_a_missing_connection_reads_as_before(broker_env):
    answer, sent = broker_env
    answer(200, {"connection_id": "c" * 32, "items": [{"event_id": "e1", "etag": "1", "status": "confirmed"}],
                 "cursor": "tok", "resync_required": False})
    db = SqliteDB()
    kinds = {"calendar": cal.CalendarSensor()}
    store.create(db, kinds, "user:7", name="s", kind="calendar")
    t = store.test(db, kinds, "user:7", "s")
    assert t["ok"] and t["counts"]["read"] == 1
    assert sent[-1]["owner"] == "7"
    answer(409, {"detail": "No ready calendar connection for this person"})
    with pytest.raises(SensorFault) as e:
        cal.broker_call("7", cal.PATH, {"owner": "7"}, session="sensor:user:7/s")
    assert "connect Google Calendar" in e.value.reason and cal.GRANT_NEEDED not in e.value.reason
