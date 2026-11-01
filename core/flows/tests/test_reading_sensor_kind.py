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
    assert refs["_evidence"] == {"connection": "pay", "reading": "pay/charges", "path": "/v1/charges",
                                 "query": "/v1/charges?created%5Bgt%5D=6400&limit=100", "cursor": "6400",
                                 "read_at": 10_000.0}
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


# ── the `github` connection (connections.github.example.json): title, state, merged_at, head sha,
#    labels of every issue and pull request, keyed so an item is read again once it changed ────────
GH_FILE = R.__file__.rsplit("/", 1)[0] + "/connections.github.example.json"


def _gh(answer, seen):
    def get(conn, path, params):
        seen.append((conn, path, dict(params)))
        return answer
    return R.ReadingSensor(get=get, config=lambda: R.load_config(GH_FILE), clock=lambda: 1_793_500_000.0)


def _gh_row(reading):
    return SensorRow(scope="instance", name=reading, kind="reading", connection_id="github/" + reading)


PULL = {"html_url": "https://github.com/Vexa-ai/vexa/pull/1962", "number": 1962, "title": "Per-account ramp limit",
        "state": "closed", "merged_at": "2026-10-31T18:02:11Z", "updated_at": "2026-10-31T18:02:12Z",
        "head": {"sha": "f916fc8efa9d2bb11c4ac9d089830c1407764a8a"}, "labels": [{"name": "bots"}], "body": "prose"}


def test_github_pulls_reading_keeps_title_state_merged_at_head_and_labels():
    seen = []
    old = {**PULL, "html_url": "https://github.com/Vexa-ai/vexa/pull/1", "updated_at": "2026-10-01T00:00:00Z"}
    t = _gh([PULL, old], seen).tick(_gh_row("vexa-pulls"), None)
    conn, path, params = seen[0]
    assert conn["base_url"] == "https://api.github.com" and conn["secret_env"] == "VEXA_GITHUB_READ_TOKEN"
    assert path == "/repos/Vexa-ai/vexa/pulls" and params["state"] == "all" and params["sort"] == "updated"
    assert [(c.event_type, c.dedupe_key) for c in t.changes] == [
        ("business.reading", "https://github.com/Vexa-ai/vexa/pull/1962@2026-10-31T18:02:12Z")]   # the old one skipped
    refs = t.changes[0].refs
    assert {k: v for k, v in refs.items() if not k.startswith("_")} == {
        "subject": PULL["html_url"], "url": PULL["html_url"], "title": "Per-account ramp limit", "state": "closed",
        "merged_at": "2026-10-31T18:02:11Z", "head_sha": "f916fc8efa9d2bb11c4ac9d089830c1407764a8a",
        "labels": [{"name": "bots"}], "updated_at": "2026-10-31T18:02:12Z"}
    ev = refs["_evidence"]
    assert ev["reading"] == "github/vexa-pulls" and ev["cursor"] == "2026-10-31T02:26:40Z"   # now - 1 day, ISO
    assert ev["query"].startswith("/repos/Vexa-ai/vexa/pulls?") and "token" not in ev["query"].lower()
    assert t.cursor == "2026-10-31T18:02:12Z"
    # the same item changed again is a new event (its key carries updated_at); unchanged, nothing
    t2 = _gh([PULL], []).tick(_gh_row("vexa-pulls"), t.cursor)
    assert t2.changes == [] and t2.cursor is None
    moved = {**PULL, "updated_at": "2026-11-01T08:00:00Z", "title": "Renamed"}
    t3 = _gh([moved], []).tick(_gh_row("vexa-pulls"), t.cursor)
    assert [c.refs["title"] for c in t3.changes] == ["Renamed"] and t3.cursor == "2026-11-01T08:00:00Z"


def test_github_issues_reading_passes_the_cursor_and_leaves_pull_requests_to_the_pulls_reading():
    seen = []
    issue = {"html_url": "https://github.com/Vexa-ai/vexa/issues/1990", "title": "An issue", "state": "open",
             "labels": [], "updated_at": "2026-11-01T07:00:00Z"}
    pr_as_issue = {**PULL, "pull_request": {"merged_at": PULL["merged_at"]}, "updated_at": "2026-11-01T07:30:00Z"}
    t = _gh([issue, pr_as_issue], seen).tick(_gh_row("vexa-issues"), "2026-11-01T00:00:00Z")
    assert seen[0][2]["since"] == "2026-11-01T00:00:00Z"
    assert [c.refs["subject"] for c in t.changes] == [issue["html_url"]]
    assert t.cursor == "2026-11-01T07:30:00Z"                  # the pull request still moves the cursor


def test_github_reading_is_get_only_with_the_token_read_by_name(monkeypatch):
    calls = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"[]"

    def fake_open(req, timeout):
        calls.update(method=req.get_method(), auth=req.get_header("Authorization"), url=req.full_url,
                     data=req.data)
        return Resp()

    monkeypatch.setattr(R.urllib.request, "urlopen", fake_open)
    monkeypatch.setenv("VEXA_GITHUB_READ_TOKEN", "ghp_test_read_only")
    conn = R.load_config(GH_FILE)["connections"]["github"]
    assert R.http_get(conn, "/repos/Vexa-ai/vexa/pulls", {"state": "all"}) == []
    assert calls["method"] == "GET" and calls["data"] is None and calls["auth"] == "Bearer ghp_test_read_only"
    assert calls["url"] == "https://api.github.com/repos/Vexa-ai/vexa/pulls?state=all"


def test_an_unknown_cursor_type_stops_the_sensor():
    cfg = {"connections": CFG["connections"],
           "readings": {"pay/x": {**CFG["readings"]["pay/charges"], "cursor_type": "weekday"}}}
    with pytest.raises(SensorFault) as e:
        R.ReadingSensor(get=lambda *a: [], config=lambda: cfg).tick(_row("pay/x"), None)
    assert e.value.retryable is False
