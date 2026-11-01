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
        "readings": {"github/pulls": {"path": "/p", "key": "id", "event_type": "business.reading"}}}))
    monkeypatch.setenv("VEXA_FLOWS_CONNECTIONS_FILE", str(f))
    monkeypatch.setenv("GH_T", "s3cr3t-value")
    monkeypatch.setattr(rk.flows_config, "get", lambda k, d=None: str(f) if k == "VEXA_FLOWS_CONNECTIONS_FILE" else d)
    r = client.get("/connections", headers=operator())
    assert r.status_code == 200, r.text
    body = r.json()
    assert {c["name"]: c["secret_set"] for c in body["connections"]} == {"github": True, "pub": None}
    assert body["readings"] == [{"id": "github/pulls", "connection": "github", "event_type": "business.reading"}]
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
