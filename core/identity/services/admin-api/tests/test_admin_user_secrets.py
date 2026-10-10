"""No `/admin/users*` read returns a stored credential. The model key and the transcription token
read as `<field>_set` plus a masked tail — the shape `/user/webhook` shows its secret in — and a
calendar feed URL as `ics_url_set` plus its masked form. The admin edit door still edits them: a
write replaces, a write of the masked read-back changes nothing, and the clear value crosses only
the internal tier the services read.

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

KEY = "sk-admin-read-never-0001"
TOKEN = "stt-admin-read-never-0002"
FEED = "https://calendar.google.com/calendar/ical/x%40vexa.ai/private-feedsecret0003/basic.ics"


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


def _internal_key(client, uid):
    return client.get(f"/internal/users/{uid}/model-config",
                      headers={"X-Internal-Secret": INTERNAL_SECRET}).json()["models"].get("api_key")


@pytest.fixture()
def person(client):
    email = "secrets@vexa.ai"
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot,tx", headers=_admin()).json()["token"]
    h = {"X-API-Key": tok}
    assert client.put("/user/models", headers=h, json={"model": "m", "api_key": KEY}).status_code == 200
    assert client.put("/user/transcription", headers=h,
                      json={"url": "https://stt.example.com", "token": TOKEN}).status_code == 200
    assert client.post("/user/calendars", headers=h, json={"name": "Work", "ics_url": FEED}).status_code == 201
    return uid, email, h


def test_no_admin_user_read_returns_a_stored_credential(client, person):
    uid, email, _h = person
    reads = [
        client.get(f"/admin/users/{uid}", headers=_admin()),
        client.get(f"/admin/users/email/{email}", headers=_admin()),
        client.post("/admin/users", headers=_admin(), json={"email": email}),      # resolve-or-create
        client.patch(f"/admin/users/{uid}", headers=_admin(), json={"max_concurrent_bots": 3}),
    ]
    for r in reads:
        assert r.status_code == 200, r.text
        for secret in (KEY, TOKEN, FEED, "private-feedsecret0003"):
            assert secret not in r.text, (r.request.url, secret)
        data = r.json()["data"]
        assert data["model_prefs"]["api_key_set"] is True and data["model_prefs"]["api_key"] == "********0001"
        assert data["transcription_prefs"]["token_set"] is True
        assert data["transcription_prefs"]["token"] == "********0002"
        feed = data["calendar_connections"][0]
        assert feed["ics_url_set"] is True and feed["ics_url_masked"] == "calendar.google.com/….ics"
        assert "ics_url" not in feed


def test_the_admin_door_edits_them_and_a_read_back_writes_nothing(client, person):
    uid, _email, h = person
    door = f"/admin/users/{uid}/models"
    r = client.put(door, headers=_admin(), json={"api_key": "sk-replaced-by-admin-9999"})
    assert r.status_code == 200 and r.json()["api_key"] == "********9999"
    assert _internal_key(client, uid) == "sk-replaced-by-admin-9999"           # a write replaces
    # the masked value a read returned, written back by the admin door or the person's own route
    shown = client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"]["model_prefs"]["api_key"]
    assert client.put(door, headers=_admin(), json={"api_key": shown, "model": "m2"}).status_code == 200
    assert client.put("/user/models", headers=h, json={"api_key": shown}).status_code == 200
    assert _internal_key(client, uid) == "sk-replaced-by-admin-9999"           # …changes nothing
    assert client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"]["model_prefs"]["model"] == "m2"
    assert client.put(door, headers=_admin(), json={"api_key": ""}).status_code == 200
    assert _internal_key(client, uid) is None                                   # an empty write clears
