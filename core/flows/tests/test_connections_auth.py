"""The one `reading` sensor kind: its auth modes (bearer with a scheme, OAuth refresh) and `GET /connections`.

What it closes: Discord answers only `Authorization: Bot …` and Google only a short-lived access token from a refresh
token, so mail and Discord were read by local scripts beside the product (founder 1 Nov 2026: «данные подключаются
через одну штуку»). And a board listing what is connected had no read of the declared connections.
"""
from __future__ import annotations

import io
import json

import pytest

from flows_sensors import reading as rk
from flows_sensors.base import SensorFault

from test_subject_bearer import admin as operator, client, fa, rows  # noqa: F401 — fixtures


class _Answer(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_bearer_scheme(monkeypatch):
    seen = {}

    def fake(req, timeout=0):
        seen["auth"] = req.get_header("Authorization")
        seen["ua"] = req.get_header("User-agent")
        return _Answer(b"[]")
    monkeypatch.setattr(rk.urllib.request, "urlopen", fake)
    monkeypatch.setenv("DISCORD_T", "tok")
    rk.http_get({"base_url": "https://x", "auth": "bearer", "scheme": "Bot", "secret_env": "DISCORD_T"}, "/a", {})
    assert seen["auth"] == "Bot tok"
    assert seen["ua"] == rk.USER_AGENT
    rk.http_get({"base_url": "https://x", "auth": "bearer", "secret_env": "DISCORD_T"}, "/a", {})
    assert seen["auth"] == "Bearer tok"


def test_oauth_refresh_exchanges_once_and_reads_with_the_access_token(monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append((req.get_method(), req.full_url, req.get_header("Authorization")))
        if req.full_url.startswith("https://token"):
            assert b"grant_type=refresh_token" in req.data and b"refresh_token=rt" in req.data
            return _Answer(json.dumps({"access_token": "at", "expires_in": 3600}).encode())
        return _Answer(b"{}")
    monkeypatch.setattr(rk.urllib.request, "urlopen", fake)
    rk._TOKENS.clear()
    for k, v in (("G_ID", "id"), ("G_SEC", "sec"), ("G_RT", "rt")):
        monkeypatch.setenv(k, v)
    conn = {"base_url": "https://src", "auth": "oauth_refresh", "token_url": "https://token/t",
            "client_id_env": "G_ID", "client_secret_env": "G_SEC", "refresh_token_env": "G_RT"}
    rk.http_get(conn, "/m", {})
    rk.http_get(conn, "/m", {})
    assert [c[0] for c in calls] == ["POST", "GET", "GET"]                 # one exchange, kept
    assert calls[1][2] == "Bearer at" and calls[0][1] == "https://token/t"


def test_oauth_refresh_without_its_secrets_is_refused(monkeypatch):
    monkeypatch.delenv("G_RT", raising=False)
    conn = {"base_url": "https://src", "auth": "oauth_refresh", "token_url": "https://token/t",
            "client_id_env": "G_ID", "client_secret_env": "G_SEC", "refresh_token_env": "G_RT"}
    with pytest.raises(SensorFault):
        rk.http_get(conn, "/m", {})
    assert rk.secret_set(conn) is False


def test_connections_lists_what_is_declared_and_never_a_secret(client, tmp_path, monkeypatch):  # noqa: F811
    f = tmp_path / "c.json"
    f.write_text(json.dumps({
        "connections": {"github": {"transport": "http_json", "base_url": "https://api.github.com", "auth": "bearer",
                                   "secret_env": "GH_T"},
                        "pub": {"transport": "http_json", "base_url": "https://p", "auth": "none"}},
        "readings": {"github/pulls": {"path": "/p", "key": "id", "event_type": "business.reading"}},
        "delivery": {"connection": "github", "pull": "/repos/{owner}/{repo}/pulls/{number}"}}))
    monkeypatch.setenv("VEXA_FLOWS_CONNECTIONS_FILE", str(f))
    monkeypatch.setenv("GH_T", "s3cr3t-value")
    monkeypatch.setattr(rk.flows_config, "get", lambda k, d=None: str(f) if k == "VEXA_FLOWS_CONNECTIONS_FILE" else d)
    r = client.get("/connections", headers=operator())
    assert r.status_code == 200, r.text
    body = r.json()
    assert {c["name"]: c["secret_set"] for c in body["connections"]} == {"github": True, "pub": None}
    assert body["readings"] == [
        {"id": "github/pulls", "connection": "github", "event_type": "business.reading", "run_by": "sensor"},
        {"id": "github/delivery", "connection": "github", "event_type": None, "run_by": "crm_card_stage_recheck"}]
    assert "s3cr3t-value" not in r.text and "GH_T" not in r.text
    assert client.get("/connections").status_code == 401


def test_first_read_baseline_sets_the_cursor_and_emits_nothing():
    from flows_sensors.base import SensorRow
    cfg = {"connections": {"d": {"transport": "http_json", "base_url": "https://d", "auth": "none"}},
           "readings": {"d/c": {"path": "/m", "params": {"after": "{cursor}"}, "items": "", "key": "id", "cursor": "id",
                                "cursor_type": "text", "first": "baseline", "keep": {"id": "id"},
                                "event_type": "business.reading"}}}
    seen = []
    row = SensorRow(scope="instance", name="d", kind="reading", connection_id="d/c")
    k = rk.ReadingSensor(get=lambda conn, path, params: seen.append(params) or [{"id": "105"}, {"id": "107"}],
                         config=lambda: cfg)
    first = k.tick(row, None)
    assert first.changes == [] and first.cursor == "107" and seen[0] == {}
    k2 = rk.ReadingSensor(get=lambda conn, path, params: seen.append(params) or [{"id": "108"}], config=lambda: cfg)
    nxt = k2.tick(row, "107")
    assert [c.dedupe_key for c in nxt.changes] == ["108"] and seen[-1] == {"after": "107"}


def test_an_absent_item_list_is_empty_only_when_declared():
    from flows_sensors.base import SensorRow
    spec = {"path": "/m", "items": "messages", "key": "id", "cursor_type": "epoch", "event_type": "business.support"}
    cfg = {"connections": {"g": {"transport": "http_json", "base_url": "https://g", "auth": "none"}},
           "readings": {"g/inbox": spec}}
    row = SensorRow(scope="instance", name="g", kind="reading", connection_id="g/inbox")
    k = rk.ReadingSensor(get=lambda conn, path, params: {"resultSizeEstimate": 0}, config=lambda: cfg)
    with pytest.raises(SensorFault):
        k.tick(row, "1")
    spec["empty_when_absent"] = True
    assert k.tick(row, "1").changes == []


def test_the_instance_holds_more_sensors_than_a_person():
    """A person keeps PER_SCOPE (its own quota tests); the instance, one sensor per declared reading, holds more."""
    from flows_sensors import store
    from sqlite_double import SqliteDB
    db = SqliteDB()
    kinds = {"reading": rk.ReadingSensor()}
    for i in range(store.PER_SCOPE + 5):
        store.create(db, kinds, "instance", name=f"i{i}", kind="reading", filter=None, connection_id="x/y",
                     interval_s=300, by="t", now=1.0)



# ── the declared report POST (founder 1 Nov 2026, Q4 «Allow POST») ───────────────────────────
GA4 = {"transport": "http_json", "base_url": "https://analyticsdata.googleapis.com", "auth": "service_account",
       "key_env": "SA_JSON", "scopes": ["https://www.googleapis.com/auth/analytics.readonly"]}


@pytest.mark.parametrize("path", ["/v1beta/properties/123:batchRunReports", "/v1beta/properties/123", "/admin/x",
                                  "/v1beta/properties/123:runReport/extra"])
def test_a_post_anywhere_else_is_refused(monkeypatch, path):
    monkeypatch.setattr(rk, "granted_scopes", lambda conn: frozenset(GA4["scopes"]))
    with pytest.raises(SensorFault):
        rk.check_post(GA4, path)


def test_the_two_report_queries_pass_on_a_read_only_grant(monkeypatch):
    monkeypatch.setattr(rk, "granted_scopes", lambda conn: frozenset(conn.get("scopes") or []))
    rk.check_post(GA4, "/v1beta/properties/123:runReport")
    gsc = {**GA4, "base_url": "https://www.googleapis.com", "scopes": ["https://www.googleapis.com/auth/webmasters.readonly"]}
    rk.check_post(gsc, "/webmasters/v3/sites/sc-domain%3Avexa.ai/searchAnalytics/query")


def test_a_grant_that_can_write_is_refused(monkeypatch):
    wide = {**GA4, "scopes": ["https://www.googleapis.com/auth/analytics"]}
    monkeypatch.setattr(rk, "granted_scopes", lambda conn: frozenset(conn["scopes"]))
    with pytest.raises(SensorFault):
        rk.check_post(wide, "/v1beta/properties/123:runReport")
    oauth = {"transport": "http_json", "base_url": "https://analyticsdata.googleapis.com", "auth": "oauth_refresh"}
    monkeypatch.setattr(rk, "granted_scopes",
                        lambda conn: frozenset({"https://www.googleapis.com/auth/analytics.readonly",
                                                "https://www.googleapis.com/auth/gmail.compose"}))
    with pytest.raises(SensorFault):
        rk.check_post(oauth, "/v1beta/properties/123:runReport")
    bearer = {**GA4, "auth": "bearer"}
    with pytest.raises(SensorFault):
        rk.check_post(bearer, "/v1beta/properties/123:runReport")


def test_a_reading_posts_its_declared_body_with_the_cursor():
    from flows_sensors.base import SensorRow
    cfg = {"connections": {"ga4": GA4},
           "readings": {"ga4/daily": {"method": "POST", "path": "/v1beta/properties/123:runReport",
                                      "body": {"dateRanges": [{"startDate": "{cursor}", "endDate": "today"}]},
                                      "items": "rows", "key": "k", "cursor_type": "text", "keep": {"k": "k"},
                                      "event_type": "business.traffic"}}}
    sent = []
    k = rk.ReadingSensor(get=lambda *a: pytest.fail("GET used"), config=lambda: cfg,
                         post=lambda conn, path, body: sent.append(body) or {"rows": [{"k": "a"}]})
    row = SensorRow(scope="instance", name="ga4", kind="reading", connection_id="ga4/daily")
    assert [c.dedupe_key for c in k.tick(row, "2026-10-31").changes] == ["a"]
    assert sent == [{"dateRanges": [{"startDate": "2026-10-31", "endDate": "today"}]}]


def test_no_other_method():
    from flows_sensors.base import SensorRow
    cfg = {"connections": {"x": {"transport": "http_json", "base_url": "https://x", "auth": "none"}},
           "readings": {"x/y": {"method": "DELETE", "path": "/a", "key": "id", "event_type": "business.reading"}}}
    k = rk.ReadingSensor(get=lambda *a: [], config=lambda: cfg, post=lambda *a: [])
    with pytest.raises(SensorFault):
        k.tick(SensorRow(scope="instance", name="x", kind="reading", connection_id="x/y"), None)



def test_a_path_steps_into_a_list():
    row = {"dimensionValues": [{"value": "20261101"}], "metricValues": [{"value": "12"}, {"value": "30"}]}
    assert rk._dig(row, "dimensionValues.0.value") == "20261101" and rk._dig(row, "metricValues.1.value") == "30"
    assert rk._dig(row, "metricValues.5.value") is None



def test_a_detail_read_fills_each_new_item_bounded_per_tick():
    from flows_sensors.base import SensorRow
    cfg = {"connections": {"g": {"transport": "http_json", "base_url": "https://g", "auth": "none"}},
           "readings": {"g/inbox": {"path": "/m", "items": "messages", "key": "id", "cursor_type": "epoch",
                                    "detail": {"path": "/m/{id}", "params": {"format": "metadata"}, "max": 2},
                                    "keep": {"message": "id", "subject": "payload.headers.0.value"},
                                    "event_type": "business.support"}}}
    calls = []

    def get(conn, path, params):
        calls.append((path, params))
        if path == "/m":
            return {"messages": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
        return {"payload": {"headers": [{"name": "From", "value": f"Ann <{path[-1]}@globex.test>"}]}}
    k = rk.ReadingSensor(get=get, config=lambda: cfg)
    out = k.tick(SensorRow(scope="instance", name="g", kind="reading", connection_id="g/inbox"), "1")
    assert [c.refs["subject"] for c in out.changes] == ["Ann <a@globex.test>", "Ann <b@globex.test>"]
    assert calls[1:] == [("/m/a", {"format": "metadata"}), ("/m/b", {"format": "metadata"})]   # max 2 per tick
