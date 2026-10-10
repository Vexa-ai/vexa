"""`PUT /user/webhook` answers with the webhook config and nothing else, and refuses a destination
this deployment would never deliver to.

A key scoped to bots or transcripts may set its account's webhook. What it reads back is the
webhook: never the account's stored model key, transcription token or calendar feed URL, which the
dedicated routes mask and which used to ride along in this response.

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

MODEL_KEY = "sk-model-key-never-echo-9f3a"
STT_TOKEN = "stt-token-never-echo-77c1"
FEED = "https://calendar.google.com/calendar/ical/x%40vexa.ai/private-feedsecret0042/basic.ics"


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


def _keys(client, email):
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    full = client.post(f"/admin/users/{uid}/tokens?scopes=bot,tx", headers=_admin()).json()["token"]
    bot = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    tx = client.post(f"/admin/users/{uid}/tokens?scopes=tx", headers=_admin()).json()["token"]
    return uid, full, bot, tx


@pytest.mark.parametrize("which", ["bot", "tx"])
def test_a_narrow_key_reads_back_the_webhook_and_no_stored_credential(client, which):
    _uid, full, bot, tx = _keys(client, f"narrow-{which}@vexa.ai")
    h = {"X-API-Key": full}
    assert client.put("/user/models", headers=h, json={
        "mode": "custom", "model": "m", "base_url": "https://llm.example.com/v1", "api_key": MODEL_KEY,
    }).status_code == 200
    assert client.put("/user/transcription", headers=h,
                      json={"url": "https://stt.example.com", "token": STT_TOKEN}).status_code == 200
    assert client.post("/user/calendars", headers=h,
                       json={"name": "Work", "ics_url": FEED, "auto_join": True}).status_code == 201

    narrow = {"bot": bot, "tx": tx}[which]
    r = client.put("/user/webhook", headers={"X-API-Key": narrow},
                   json={"webhook_url": "https://hooks.example.com/vexa", "webhook_secret": "whsec-0123456789",
                         "webhook_events": {"meeting.completed": True}})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "webhook_url": "https://hooks.example.com/vexa",
        "webhook_secret_set": True,
        "webhook_secret": "********6789",
        "webhook_events": {"meeting.completed": True},
    }
    for secret in (MODEL_KEY, STT_TOKEN, "private-feedsecret0042", "whsec-0123456789"):
        assert secret not in r.text
    assert r.json() == client.get("/user/webhook", headers={"X-API-Key": narrow}).json()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/hook", "http://[::ffff:127.0.0.1]/hook", "http://[::ffff:169.254.169.254]/",
    "http://[64:ff9b::a9fe:a9fe]/", "http://[2002:a9fe:a9fe::1]/", "http://[::10.0.0.1]/",
    "http://169.254.169.254/latest", "http://2130706433/", "http://redis:6379/", "http://localhost/hook",
    "http://metadata.google.internal/", "ftp://example.com/hook",
])
def test_an_internal_webhook_destination_is_refused_when_saved(client, url):
    _uid, full, _bot, _tx = _keys(client, "dest@vexa.ai")
    r = client.put("/user/webhook", headers={"X-API-Key": full}, json={"webhook_url": url})
    assert r.status_code == 422, r.text
    assert client.get("/user/webhook", headers={"X-API-Key": full}).json()["webhook_url"] is None


def test_clearing_the_webhook_still_works(client):
    _uid, full, _bot, _tx = _keys(client, "clear@vexa.ai")
    h = {"X-API-Key": full}
    assert client.put("/user/webhook", headers=h, json={"webhook_url": "https://hooks.example.com/x"}).status_code == 200
    r = client.put("/user/webhook", headers=h, json={"webhook_url": ""})
    assert r.status_code == 200 and r.json()["webhook_url"] == ""


@pytest.mark.parametrize("url", [
    "http://[::ffff:127.0.0.1]/feed.ics", "http://[64:ff9b::a9fe:a9fe]/feed.ics", "http://10.0.0.5/feed.ics",
    "http://169.254.169.254/feed.ics", "http://admin-api:8001/feed.ics", "http://0x7f.0.0.1/feed.ics",
])
def test_an_internal_calendar_feed_is_refused_when_saved(client, url):
    _uid, full, _bot, _tx = _keys(client, "feed@vexa.ai")
    h = {"X-API-Key": full}
    assert client.post("/user/calendars", headers=h, json={"name": "x", "ics_url": url}).status_code == 422
    assert client.put("/user/calendar", headers=h, json={"ics_url": url}).status_code == 422
