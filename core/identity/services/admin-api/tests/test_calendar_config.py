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
ICS_2 = "https://outlook.office365.com/owa/calendar/private-fedcba654321/calendar.ics"


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


def test_calendar_rejects_embed_page_url(client):
    """The #1 paste mistake: Google Calendar's EMBED page (HTML) instead of the ICS feed. The
    422 must TEACH — name the 'Secret address in iCal format' fix, not just refuse."""
    _uid, tok = _user_token(client, email="cal-embed@vexa.ai")
    r = client.put("/user/calendar", headers={"X-API-Key": tok},
                   json={"ics_url": "https://calendar.google.com/calendar/embed?src=x%40vexa.ai&ctz=Europe%2FLisbon"})
    assert r.status_code == 422
    assert "Secret address in iCal format" in r.json()["detail"]


def test_calendar_auto_join_defaults_true(client):
    _uid, tok = _user_token(client, email="cal3@vexa.ai")
    r = client.get("/user/calendar", headers={"X-API-Key": tok})
    assert r.json()["auto_join"] is True


def test_calendar_bot_name_is_user_visible_and_reaches_auto_join_context(client):
    uid, tok = _user_token(client, email="cal-name@vexa.ai")
    h = {"X-API-Key": tok}

    assert client.get("/user/calendar", headers=h).json()["bot_name"] == "Vexa"
    updated = client.put("/user/calendar", headers=h, json={"bot_name": "  Note Taker  "})
    assert updated.status_code == 200, updated.text
    assert updated.json()["bot_name"] == "Note Taker"

    ctx = client.get(f"/internal/users/{uid}/bot-context",
                     headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["bot_name"] == "Note Taker"

    rejected = client.put("/user/calendar", headers=h, json={"bot_name": " "})
    assert rejected.status_code == 422


def test_legacy_bot_name_updates_first_calendar_connection(client):
    uid, tok = _user_token(client, email="cal-legacy-name@vexa.ai")
    h = {"X-API-Key": tok}
    client.put("/user/calendar", headers=h, json={"ics_url": ICS})
    updated = client.put("/user/calendar", headers=h, json={"bot_name": "Legacy Notes"})
    assert updated.status_code == 200
    (calendar,) = client.get("/user/calendars", headers=h).json()["calendars"]
    assert calendar["bot_name"] == "Legacy Notes"
    internal = client.get("/internal/calendar-configs",
                          headers={"X-Internal-Secret": INTERNAL_SECRET}).json()["configs"]
    cfg = next(item for item in internal if item["user_id"] == uid)
    assert cfg["bot_name"] == "Legacy Notes"


def test_plural_calendars_are_independent_and_never_echo_secret_urls(client):
    uid, tok = _user_token(client, email="cal-many@vexa.ai")
    h = {"X-API-Key": tok}

    work = client.post("/user/calendars", headers=h,
                       json={"name": "Work", "ics_url": ICS, "auto_join": True,
                             "bot_name": "Work Notes"})
    personal = client.post("/user/calendars", headers=h,
                           json={"name": "Personal", "ics_url": ICS_2, "auto_join": False})
    assert work.status_code == 201, work.text
    assert personal.status_code == 201, personal.text
    assert work.json()["id"] != personal.json()["id"]
    assert work.json()["bot_name"] == "Work Notes"
    assert personal.json()["bot_name"] == "Vexa"

    listed = client.get("/user/calendars", headers=h).json()["calendars"]
    assert [item["name"] for item in listed] == ["Work", "Personal"]
    rendered = str(listed)
    assert "private-abc123def456" not in rendered
    assert "private-fedcba654321" not in rendered

    changed = client.patch(f"/user/calendars/{personal.json()['id']}", headers=h,
                           json={"name": "Family", "auto_join": True,
                                 "bot_name": "Family Notes"})
    assert changed.status_code == 200
    assert changed.json()["name"] == "Family"
    assert changed.json()["auto_join"] is True
    assert changed.json()["bot_name"] == "Family Notes"

    removed = client.delete(f"/user/calendars/{work.json()['id']}", headers=h)
    assert removed.status_code == 204
    remaining = client.get("/user/calendars", headers=h).json()["calendars"]
    assert [(item["name"], item["id"]) for item in remaining] == [
        ("Family", personal.json()["id"]),
    ]

    internal = client.get("/internal/calendar-configs",
                          headers={"X-Internal-Secret": INTERNAL_SECRET}).json()["configs"]
    active = next(c for c in internal if c.get("calendar_id") == personal.json()["id"])
    assert active == {
        "user_id": uid, "calendar_id": personal.json()["id"], "calendar_name": "Family",
        "ics_url": ICS_2, "auto_join": True, "bot_name": "Family Notes",
    }
    retired = next(c for c in internal if c.get("calendar_id") == work.json()["id"])
    # The first connection is the legacy claimant even as a tombstone: its sweep
    # must still match bare-calendar_uid rows so deleting it retires them.
    assert retired == {
        "user_id": uid, "calendar_id": work.json()["id"], "calendar_name": "Work",
        "bot_name": "Work Notes", "deleted": True, "legacy": True,
    }

    rejected = client.patch(f"/user/calendars/{personal.json()['id']}", headers=h,
                            json={"bot_name": " "})
    assert rejected.status_code == 422


def test_legacy_calendar_endpoint_materializes_first_plural_connection(client):
    _uid, tok = _user_token(client, email="cal-legacy@vexa.ai")
    h = {"X-API-Key": tok}
    legacy = client.put("/user/calendar", headers=h,
                        json={"ics_url": ICS, "auto_join": False})
    assert legacy.status_code == 200
    plural = client.get("/user/calendars", headers=h).json()["calendars"]
    assert len(plural) == 1
    assert plural[0]["name"] == "Calendar"
    assert plural[0]["auto_join"] is False

    updated = client.patch(f"/user/calendars/{plural[0]['id']}", headers=h,
                           json={"name": "Migrated", "auto_join": True})
    assert updated.status_code == 200
    legacy_read = client.get("/user/calendar", headers=h).json()
    assert legacy_read["ics_url_set"] is True
    assert legacy_read["auto_join"] is True


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
    assert any(c["user_id"] == uid and c.get("ics_url") == ICS and c["auto_join"] is True
               and c.get("calendar_id") and c.get("calendar_name") == "Calendar"
               for c in configs)
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
