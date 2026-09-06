"""Person facts move to identity — `/user/settings` and the internal edge flows reads.

WHY THEY MOVE. `.settings.json` lived in a workspace in the AGENT domain, written by the control
MCP and read by flows (`flows_steps/common.py:setting`). That made two domains depend on a third for
a fact about a PERSON: with no agent domain deployed a person had no timezone and no mail
preferences, so flows could not state a time in their clock or honour "stop mailing me minutes".
Identity is the only domain everyone may depend on, and these six keys are facts about the person —
so identity owns them.

WHAT IS NOT HERE. `bot_name` is a fact about the BOT, not the person, and it already has a home on
this line (`users.data.calendar_bot_name` → `/internal/users/{id}/bot-context` → meeting-api's
auto-join). It is deliberately absent from this vocabulary; see the PR.

Same testcontainers-PG harness as the other identity suites (skips without docker).
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


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _user(client, email="settings@vexa.ai"):
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    return uid, {"X-API-Key": tok}


# ── the internal edge flows reads ────────────────────────────────────────────────────────────
def test_the_internal_edge_is_closed_without_the_secret(client):
    uid, _h = _user(client)
    assert client.get(f"/internal/users/{uid}/settings").status_code in (401, 403)


def test_an_unknown_user_is_a_404_not_a_silent_default(client):
    """Defaults for a person who exists and defaults for a person who does not are opposite facts:
    the second one means flows is about to mail somebody who is not there."""
    assert client.get("/internal/users/999999/settings", headers=_internal()).status_code == 404


# ── A17: the write door, the importer, and the dev-mode bypass ─────────────────────────────────
# The read door shipped ALONE: `person_settings.apply` and `plan_import` had no caller anywhere, so
# identity could only answer DEFAULTS — everybody who had turned their minutes off started
# receiving them again on upgrade, and everybody outside UTC had their times stated in the wrong
# clock. These are the round trips that prove the halves are joined.

def test_the_write_door_changes_what_the_read_door_serves(client):
    uid, _h = _user(client, "writer@vexa.ai")
    assert client.get(f"/internal/users/{uid}/settings", headers=_internal()).json() == {
        "timezone": "", "mail_minutes": True, "mail_join": False,
        "mail_rsvp": True, "mail_prep": True}

    r = client.put(f"/internal/users/{uid}/settings", headers=_internal(),
                   json={"mail_minutes": "off", "timezone": "Europe/Lisbon"})
    assert r.status_code == 200, r.text
    assert r.json()["mail_minutes"] is False
    assert r.json()["timezone"] == "Europe/Lisbon"

    served = client.get(f"/internal/users/{uid}/settings", headers=_internal()).json()
    assert served["mail_minutes"] is False and served["timezone"] == "Europe/Lisbon"
    # PARTIAL: the keys not sent are untouched, not reset.
    assert served["mail_rsvp"] is True and served["mail_prep"] is True


def test_a_refused_value_changes_nothing_at_all(client):
    """All-or-nothing. A half-applied change is a person who believes they turned two things off
    and turned one."""
    uid, _h = _user(client, "allornothing@vexa.ai")
    r = client.put(f"/internal/users/{uid}/settings", headers=_internal(),
                   json={"mail_join": "on", "timezone": "Not/AZone"})
    assert r.status_code == 422
    assert client.get(f"/internal/users/{uid}/settings",
                      headers=_internal()).json()["mail_join"] is False


def test_the_write_door_refuses_an_unknown_key_with_the_vocabulary(client):
    uid, _h = _user(client, "vocab@vexa.ai")
    r = client.put(f"/internal/users/{uid}/settings", headers=_internal(),
                   json={"mail_everything": "off"})
    assert r.status_code == 422
    assert "the_settings_that_exist" in r.json()["detail"]


def test_the_write_door_is_closed_without_the_secret(client):
    uid, _h = _user(client, "closed@vexa.ai")
    assert client.put(f"/internal/users/{uid}/settings",
                      json={"mail_minutes": "off"}).status_code in (401, 403)


def test_an_unknown_user_is_a_404_on_the_write_door_too(client):
    assert client.put("/internal/users/999999/settings", headers=_internal(),
                      json={"mail_minutes": "off"}).status_code == 404


def test_the_importer_migrates_a_settings_json_and_is_re_runnable(client):
    """The operator-driven one-shot migration off `.settings.json`. Its three rules ARE the
    contract: an already-set key is kept, `bot_name` lands in the bot's own store only when that
    store is empty, an unknown key is dropped rather than refusing the whole file."""
    uid, _h = _user(client, "migrate@vexa.ai")
    legacy = {"timezone": "Europe/Lisbon", "mail_minutes": False,
              "bot_name": "Notes", "colour": "blue"}

    r = client.post(f"/admin/users/{uid}/settings/import", headers=_admin(), json=legacy)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["imported"]) == {"timezone", "mail_minutes", "bot_name"}
    assert body["dropped"] == ["colour"]
    assert body["settings"]["bot_name"] == "Notes"

    served = client.get(f"/internal/users/{uid}/settings", headers=_internal()).json()
    assert served["timezone"] == "Europe/Lisbon" and served["mail_minutes"] is False

    # The person then changes their mind through the new door…
    client.put(f"/internal/users/{uid}/settings", headers=_internal(), json={"mail_minutes": "on"})
    # …and a SECOND run of the same migration must not undo it.
    again = client.post(f"/admin/users/{uid}/settings/import", headers=_admin(), json=legacy).json()
    assert "mail_minutes" in again["kept"]
    assert client.get(f"/internal/users/{uid}/settings",
                      headers=_internal()).json()["mail_minutes"] is True


def test_the_imported_bot_name_reaches_the_door_meetings_actually_reads(client):
    """Into `users.data.calendar_bot_name` — the store `/internal/users/{id}/bot-context` serves —
    and not into a fourth copy of one fact."""
    uid, _h = _user(client, "botname@vexa.ai")
    client.post(f"/admin/users/{uid}/settings/import", headers=_admin(), json={"bot_name": "Notes"})
    ctx = client.get(f"/internal/users/{uid}/bot-context", headers=_internal()).json()
    assert ctx["bot_name"] == "Notes"
    # …and it is NOT a person setting: the read door does not serve it.
    assert "bot_name" not in client.get(f"/internal/users/{uid}/settings",
                                        headers=_internal()).json()


def test_dev_mode_does_not_open_the_cross_user_settings_doors(client, monkeypatch):
    """The route's PATH names the person, so an unauthenticated dev-mode answer is a cross-user
    read of somebody's private preferences. `_check_internal`'s bypass does not apply here."""
    uid, _h = _user(client, "devmode@vexa.ai")
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    assert client.get(f"/internal/users/{uid}/settings").status_code == 503
    assert client.put(f"/internal/users/{uid}/settings",
                      json={"mail_minutes": "off"}).status_code == 503
