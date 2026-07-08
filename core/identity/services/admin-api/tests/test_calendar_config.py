"""Calendar-sync config (identity side) — /user/calendar self-serve + the internal edges.

The ICS feed URL is a SECRET (Google/Outlook secret-address feeds): stored in user.data JSONB,
masked on every user-facing read, surfaced in the clear ONLY over the X-Internal-Secret edge that
meeting-api's poller calls. `/internal/users/{id}/bot-context` is the auto-join sweep's stand-in
for the spawn-context headers the gateway injects on POST /bots.

Same testcontainers-PG harness as O-STACK-3 (skips without docker).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

ICS = "https://calendar.google.com/calendar/ical/bob%40vexa.ai/private-abc123def456/basic.ics"


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _user_token(client, email="cal@vexa.ai", max_bots=4):
    uid = client.post("/admin/users", headers=_admin(),
                      json={"email": email, "max_concurrent_bots": max_bots}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    return uid, tok


def test_calendar_set_read_masked_and_disconnect(client):
    _uid, tok = _user_token(client)
    h = {"X-API-Key": tok}

    r = client.put("/user/calendar", headers=h, json={"ics_url": ICS, "auto_join": False})
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["ics_url_set"] is True
    assert cfg["auto_join"] is False
    # masked: host + tail only — the secret path NEVER echoes
    assert "private-abc123def456" not in cfg["ics_url_masked"]
    assert cfg["ics_url_masked"].startswith("calendar.google.com")

    r = client.get("/user/calendar", headers=h)
    assert r.json()["ics_url_set"] is True

    # disconnect
    r = client.put("/user/calendar", headers=h, json={"ics_url": None})
    assert r.status_code == 200
    assert r.json()["ics_url_set"] is False


def test_calendar_rejects_non_http_url(client):
    _uid, tok = _user_token(client, email="cal2@vexa.ai")
    r = client.put("/user/calendar", headers={"X-API-Key": tok},
                   json={"ics_url": "file:///etc/passwd"})
    assert r.status_code == 422


def test_calendar_auto_join_defaults_true(client):
    _uid, tok = _user_token(client, email="cal3@vexa.ai")
    r = client.get("/user/calendar", headers={"X-API-Key": tok})
    assert r.json()["auto_join"] is True


def test_internal_calendar_configs_secret_gated(client):
    uid, tok = _user_token(client, email="cal4@vexa.ai")
    client.put("/user/calendar", headers={"X-API-Key": tok}, json={"ics_url": ICS})

    # wrong/missing secret → fail closed
    assert client.get("/internal/calendar-configs").status_code == 403
    assert client.get("/internal/calendar-configs",
                      headers={"X-Internal-Secret": "nope"}).status_code == 403

    r = client.get("/internal/calendar-configs", headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200, r.text
    configs = r.json()["configs"]
    assert {"user_id": uid, "ics_url": ICS, "auto_join": True} in configs
    # only users WITH a feed appear
    assert all(c["ics_url"] for c in configs)


def test_internal_bot_context(client):
    uid, tok = _user_token(client, email="cal5@vexa.ai", max_bots=4)
    client.put("/user/webhook", headers={"X-API-Key": tok},
               json={"webhook_url": "https://example.com/hook", "webhook_secret": "shh"})

    assert client.get(f"/internal/users/{uid}/bot-context").status_code == 403

    r = client.get(f"/internal/users/{uid}/bot-context",
                   headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["max_concurrent"] == 4
    assert ctx["webhook_url"] == "https://example.com/hook"
    assert ctx["webhook_secret"] == "shh"

    # unknown user → 404
    r = client.get("/internal/users/999999/bot-context",
                   headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert r.status_code == 404
