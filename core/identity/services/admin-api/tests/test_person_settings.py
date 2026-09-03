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


# ── the person's own door ────────────────────────────────────────────────────────────────────
def test_a_person_who_has_set_nothing_gets_the_documented_defaults(client):
    """Never empty, and never a missing key: a caller that has to distinguish "unset" from "off"
    will get it wrong, and the wrong way round is a person who stops receiving their minutes."""
    _uid, h = _user(client)
    body = client.get("/user/settings", headers=h).json()
    assert body["settings"] == {"timezone": "", "mail_minutes": True, "mail_join": False,
                                "mail_rsvp": True, "mail_prep": True, "bot_name": "Vexa"}
    assert set(body["what_each_means"]) == set(body["settings"])


def test_one_key_changes_and_the_others_do_not(client):
    _uid, h = _user(client)
    r = client.put("/user/settings", headers=h, json={"mail_minutes": False})
    assert r.status_code == 200, r.text
    after = r.json()["settings"]
    assert after["mail_minutes"] is False
    assert after["mail_rsvp"] is True and after["mail_prep"] is True


def test_the_vocabulary_is_closed_and_the_refusal_carries_it(client):
    """A setting that silently does nothing is worse than an error, and an agent with no vocabulary
    invents one — so the refusal hands back the list rather than saying no."""
    _uid, h = _user(client)
    r = client.put("/user/settings", headers=h, json={"make_it_funnier": True})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "make_it_funnier" in str(detail)
    assert set(detail["the_settings_that_exist"]) == {
        "timezone", "mail_minutes", "mail_join", "mail_rsvp", "mail_prep", "bot_name"}


def test_bot_name_is_settable_here_but_stored_where_meetings_reads_it(client):
    """A bot default is a fact about the BOT, and its store already exists. This door writes THAT
    key rather than a second one — see the bot-fact tests at the end of this file."""
    _uid, h = _user(client)
    assert client.put("/user/settings", headers=h,
                      json={"bot_name": "Scribe"}).status_code == 200


def test_a_timezone_that_is_not_a_zone_is_refused(client):
    _uid, h = _user(client)
    assert client.put("/user/settings", headers=h,
                      json={"timezone": "Mars/Olympus"}).status_code == 422
    r = client.put("/user/settings", headers=h, json={"timezone": "Europe/Lisbon"})
    assert r.status_code == 200 and r.json()["settings"]["timezone"] == "Europe/Lisbon"


def test_an_on_off_key_takes_the_words_a_person_uses(client):
    """The MCP tool passes through whatever the person said. Parsing it HERE means one parser, in
    the domain that owns the value — not one in every caller."""
    _uid, h = _user(client)
    for word, expected in (("off", False), ("yes", True), ("0", False), (True, True)):
        r = client.put("/user/settings", headers=h, json={"mail_join": word})
        assert r.status_code == 200, (word, r.text)
        assert r.json()["settings"]["mail_join"] is expected


def test_settings_are_per_person(client):
    _uid_a, ha = _user(client, "a@vexa.ai")
    _uid_b, hb = _user(client, "b@vexa.ai")
    client.put("/user/settings", headers=ha, json={"mail_minutes": False})
    assert client.get("/user/settings", headers=hb).json()["settings"]["mail_minutes"] is True


def test_the_door_needs_a_credential(client):
    assert client.get("/user/settings").status_code == 401
    assert client.put("/user/settings", json={"mail_join": True}).status_code == 401


# ── the internal edge flows reads ────────────────────────────────────────────────────────────
def test_flows_reads_one_person_s_settings_over_the_internal_edge(client):
    """flows may call identity — that is an allowed door. It may NOT call the agent domain, which
    is what reading `.settings.json` was."""
    uid, h = _user(client)
    client.put("/user/settings", headers=h, json={"timezone": "Europe/Lisbon",
                                                  "mail_prep": False})
    r = client.get(f"/internal/users/{uid}/settings", headers=_internal())
    assert r.status_code == 200, r.text
    assert r.json() == {"timezone": "Europe/Lisbon", "mail_minutes": True, "mail_join": False,
                        "mail_rsvp": True, "mail_prep": False}


def test_the_internal_edge_is_closed_without_the_secret(client):
    uid, _h = _user(client)
    assert client.get(f"/internal/users/{uid}/settings").status_code in (401, 403)


def test_an_unknown_user_is_a_404_not_a_silent_default(client):
    """Defaults for a person who exists and defaults for a person who does not are opposite facts:
    the second one means flows is about to mail somebody who is not there."""
    assert client.get("/internal/users/999999/settings", headers=_internal()).status_code == 404


# ── the one-shot migration off `.settings.json` ──────────────────────────────────────────────
def test_a_legacy_settings_file_imports_every_key_that_has_a_home(client):
    """The migration takes a `.settings.json` verbatim: the five person facts land in identity,
    `bot_name` lands in the BOT's own store, and a key nobody ever supported is ignored rather than
    refused — a migration that stops on one odd key leaves half the estate on the old store, and
    there is no second run that fixes that."""
    uid, h = _user(client, "legacy@vexa.ai")
    legacy = {"bot_name": "Scribe", "timezone": "Europe/Lisbon", "mail_minutes": False,
              "who_knows": 7}
    r = client.post(f"/internal/users/{uid}/settings/import", headers=_internal(), json=legacy)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == {"bot_name": "Scribe", "timezone": "Europe/Lisbon",
                                    "mail_minutes": False}
    assert r.json()["dropped"] == ["who_knows"]
    assert client.get("/user/settings", headers=h).json()["settings"]["timezone"] == "Europe/Lisbon"


def test_the_import_never_overwrites_a_setting_the_person_already_changed(client):
    """IDEMPOTENT AND LOSSLESS. The migration may be re-run — against an estate where somebody has
    since set a preference through the new door. A second run that clobbered it would silently undo
    a person's choice, which is the one thing a migration must never do."""
    uid, h = _user(client, "already@vexa.ai")
    client.put("/user/settings", headers=h, json={"mail_minutes": False})
    r = client.post(f"/internal/users/{uid}/settings/import", headers=_internal(),
                    json={"mail_minutes": True, "mail_rsvp": False})
    assert r.status_code == 200, r.text
    after = client.get("/user/settings", headers=h).json()["settings"]
    assert after["mail_minutes"] is False, "the person's own change won"
    assert after["mail_rsvp"] is False, "an untouched key still imported"
    assert r.json()["kept"] == ["mail_minutes"]


def test_the_import_is_closed_without_the_internal_secret(client):
    uid, _h = _user(client, "closed@vexa.ai")
    assert client.post(f"/internal/users/{uid}/settings/import",
                       json={"timezone": "UTC"}).status_code in (401, 403)


# ── the bot fact: settable here, stored in the ONE place meetings reads ──────────────────────
def test_bot_name_is_settable_and_lands_in_the_store_meetings_already_reads(client):
    """NO FOURTH STORE (founder ruling). `users.data.calendar_bot_name` is the single source —
    served on /internal/users/{id}/bot-context, which meeting-api reads on both spawn paths. This
    door writes THAT key, so a name set here and a name set on the calendar screen are one thing."""
    uid, h = _user(client, "botname@vexa.ai")
    assert client.get("/user/settings", headers=h).json()["settings"]["bot_name"] == "Vexa"
    r = client.put("/user/settings", headers=h, json={"bot_name": "Scribe"})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["bot_name"] == "Scribe"
    # the SAME value the meetings domain resolves a spawn from
    ctx = client.get(f"/internal/users/{uid}/bot-context", headers=_internal()).json()
    assert ctx["bot_name"] == "Scribe"


def test_the_calendar_screen_and_this_door_set_one_value(client):
    uid, h = _user(client, "onestore@vexa.ai")
    client.put("/user/settings", headers=h, json={"bot_name": "FromChat"})
    assert client.get(f"/internal/users/{uid}/bot-context",
                      headers=_internal()).json()["bot_name"] == "FromChat"


def test_bot_name_is_absent_from_the_internal_settings_edge(client):
    """flows does not read a bot name any more — meetings resolves it. Serving it here would invite
    exactly the second reader this move removed."""
    uid, h = _user(client, "notflows@vexa.ai")
    client.put("/user/settings", headers=h, json={"bot_name": "Scribe"})
    assert "bot_name" not in client.get(f"/internal/users/{uid}/settings",
                                        headers=_internal()).json()


def test_an_empty_bot_name_is_refused(client):
    _uid, h = _user(client, "emptyname@vexa.ai")
    assert client.put("/user/settings", headers=h, json={"bot_name": "  "}).status_code == 422


def test_the_migration_leaves_an_existing_persons_bot_name_unchanged(client):
    """THE POINT OF MIGRATING RATHER THAN DELETING. Somebody who set a name in chat keeps it, and
    somebody who set one on the calendar screen keeps THAT — the import never changes the name a
    person's bot shows up as, in either direction."""
    # (a) a person whose only name was in the workspace file: it arrives, and the bot keeps it
    uid_a, _ha = _user(client, "only-chat@vexa.ai")
    r = client.post(f"/internal/users/{uid_a}/settings/import", headers=_internal(),
                    json={"bot_name": "Scribe", "timezone": "Europe/Lisbon"})
    assert r.status_code == 200, r.text
    assert r.json()["imported"]["bot_name"] == "Scribe"
    assert client.get(f"/internal/users/{uid_a}/bot-context",
                      headers=_internal()).json()["bot_name"] == "Scribe"

    # (b) a person who already had one on the calendar side: the import does NOT overwrite it
    uid_b, hb = _user(client, "already-named@vexa.ai")
    client.put("/user/settings", headers=hb, json={"bot_name": "Calendarius"})
    r = client.post(f"/internal/users/{uid_b}/settings/import", headers=_internal(),
                    json={"bot_name": "Scribe"})
    assert r.json()["kept"] == ["bot_name"]
    assert client.get(f"/internal/users/{uid_b}/bot-context",
                      headers=_internal()).json()["bot_name"] == "Calendarius"

    # (c) re-running the migration changes nothing
    before = client.get(f"/internal/users/{uid_a}/bot-context", headers=_internal()).json()
    client.post(f"/internal/users/{uid_a}/settings/import", headers=_internal(),
                json={"bot_name": "SomethingElse"})
    assert client.get(f"/internal/users/{uid_a}/bot-context",
                      headers=_internal()).json() == before
