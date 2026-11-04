"""The generic `reading` sensor kind: config resolution, cursor, where/keep, evidence, read-only."""
import json

import pytest

from flows_sensors.base import SensorFault, SensorRow
from flows_sensors import reading as R
from flows_sensors import pool

CFG = {"connections": {"pay": {"transport": "http_json", "base_url": "https://pay.example",
                               "auth": "bearer", "secret_env": "PAY_KEY"}},
       "readings": {"pay/charges": {"path": "/v1/charges", "params": {"created[gt]": "{cursor}", "limit": "100"},
                                    "items": "data", "key": "id", "cursor": "created",
                                    "where": {"status": "succeeded"},
                                    "keep": {"email": "billing.email", "amount": "amount"},
                                    "event_type": "business.paid", "first_window_s": 3600}}}


def _row(conn="pay/charges"):
    return SensorRow(scope="instance", name="stripe-paid", kind="reading", connection_id=conn)


def _kind(answer, seen):
    def get(conn, path, params):
        seen.append((conn["base_url"], path, dict(params)))
        return answer
    return R.ReadingSensor(get=get, config=lambda: CFG, clock=lambda: 10_000.0)


def test_first_read_looks_back_one_window_and_keeps_only_matching_items():
    seen = []
    ans = {"data": [{"id": "ch_2", "created": 9_500, "status": "succeeded", "amount": 1200, "billing": {"email": "a@x.io"}},
                    {"id": "ch_1", "created": 9_000, "status": "failed", "amount": 50}]}
    t = _kind(ans, seen).tick(_row(), None)
    assert seen == [("https://pay.example", "/v1/charges", {"created[gt]": "6400", "limit": "100"})]
    assert [(c.event_type, c.dedupe_key) for c in t.changes] == [("business.paid", "ch_2")]
    refs = t.changes[0].refs
    assert refs["email"] == "a@x.io" and refs["amount"] == 1200
    assert refs["_evidence"] == {"connection": "pay", "reading": "pay/charges", "path": "/v1/charges", "read_at": 10_000.0}
    assert t.cursor == "9500"                       # the failed item still moves the cursor


def test_cursor_unchanged_and_old_items_skipped():
    seen = []
    t = _kind({"data": [{"id": "ch_2", "created": 9_500, "status": "succeeded"}]}, seen).tick(_row(), "9500")
    assert seen[0][2]["created[gt]"] == "9500"
    assert t.changes == [] and t.cursor is None


def test_unknown_reading_stops_the_sensor():
    with pytest.raises(SensorFault) as e:
        _kind({}, []).tick(_row("pay/nope"), None)
    assert e.value.retryable is False


def test_secret_is_read_by_name_and_never_echoed(monkeypatch):
    calls = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"data": []}'

    def fake_open(req, timeout):
        calls["auth"] = req.get_header("Authorization")
        calls["method"] = req.get_method()
        return Resp()

    monkeypatch.setattr(R.urllib.request, "urlopen", fake_open)
    monkeypatch.setenv("PAY_KEY", "sk_test_secret")
    assert R.http_get(CFG["connections"]["pay"], "/v1/charges", {}) == {"data": []}
    assert calls == {"auth": "Bearer sk_test_secret", "method": "GET"}
    monkeypatch.delenv("PAY_KEY")
    with pytest.raises(SensorFault) as e:
        R.http_get(CFG["connections"]["pay"], "/v1/charges", {})
    assert "sk_test" not in e.value.reason and e.value.retryable is False


def test_registered_and_instance_only():
    from flows_sensors.calendar import KINDS
    k = KINDS["reading"]
    assert k.allowed_in("instance") and not k.allowed_in("user:7")


def test_pool_dry_run_reports_would_emit(tmp_path, monkeypatch):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(CFG))
    monkeypatch.setenv("VEXA_FLOWS_CONNECTIONS_FILE", str(f))
    k = R.ReadingSensor(get=lambda c, p, q: {"data": [{"id": "ch_9", "created": 9_999, "status": "succeeded"}]},
                        clock=lambda: 10_000.0)
    out = pool.dry_run(k, _row())
    assert out["ok"] and out["counts"]["would_emit"] == 1
