"""A17 — the person-settings WRITE door and the `.settings.json` importer exist and are closed.

WHAT SHIPPED BROKEN. The read door (`GET /internal/users/{id}/settings`) shipped alone: flows
started reading `timezone` and the `mail_*` preferences from identity, while `person_settings.apply`
and `person_settings.plan_import` had NO CALLER ANYWHERE. Identity could therefore only ever answer
DEFAULTS — so on upgrade every person who had turned their minutes off started receiving them
again, and everybody outside UTC had their times stated in the wrong clock. The vocabulary moved
here to end "mail everybody everything, in UTC" and, wired up halfway, produced it.

AND THE READ DOOR WAS OPEN IN DEV MODE. `_check_internal` lets `DEV_MODE=true` with no
`INTERNAL_API_SECRET` through unauthenticated. On `/internal/validate` that is a local convenience;
on a route whose PATH names the person, it is a cross-user read of private preferences by any
caller. Both settings doors now use `_check_internal_no_dev_bypass`.

Offline — routes, refusals and the pure decision functions. No docker. The DB-backed round trips
(write → read, import → keep/drop) are in `test_person_settings.py`, which needs a real Postgres.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api.app import db as app_db
from admin_api.app import person_settings as ps
from admin_api.app.main import create_app

SECRET = "internal-secret-for-the-doors-test"
ADMIN = "admin-token-for-the-doors-test"


@pytest.fixture()
def client(monkeypatch):
    """The app with the DB dependency stubbed out — every assertion below refuses BEFORE any
    session is used, which is the property being tested."""
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    app = create_app()

    async def _no_db():
        yield None

    app.dependency_overrides[app_db.get_db] = _no_db
    with TestClient(app) as c:
        yield c


def _routes(app):
    return {(m, r.path) for r in app.routes for m in getattr(r, "methods", set()) or set()}


# ── the doors exist at all (the A17 defect was an absence, not a bug) ──────────────────────────
def test_the_write_door_and_the_importer_are_routed(client):
    routes = _routes(client.app)
    assert ("GET", "/internal/users/{user_id}/settings") in routes
    assert ("PUT", "/internal/users/{user_id}/settings") in routes, (
        "identity serves person settings and nothing can write them — every preference reverts "
        "to defaults on upgrade")
    assert ("POST", "/admin/users/{user_id}/settings/import") in routes, (
        "`plan_import` is the one-shot migration off .settings.json and has no route to fire it")


# ── the dev-mode bypass is gone from the per-person doors ──────────────────────────────────────
@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_dev_mode_does_not_open_a_cross_user_settings_door(client, monkeypatch, method):
    """`DEV_MODE=true` + no secret used to answer this route unauthenticated — for ANY user id the
    caller cares to type. It is a 503 now: dev mode still works, it just has to name a secret."""
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    r = client.request(method, "/internal/users/1/settings", json={"timezone": "UTC"})
    assert r.status_code == 503
    assert "never open" in str(r.json()["detail"])


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_a_wrong_or_missing_secret_is_refused_with_a_secret_configured(client, monkeypatch, method):
    monkeypatch.setenv("DEV_MODE", "true")          # still true — and still no help
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    assert client.request(method, "/internal/users/1/settings", json={}).status_code == 403
    assert client.request(method, "/internal/users/1/settings", json={},
                          headers={"X-Internal-Secret": "wrong"}).status_code == 403


def test_the_importer_is_admin_gated(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    r = client.post("/admin/users/1/settings/import", json={"timezone": "UTC"})
    assert r.status_code in (401, 403)


# ── the write door's vocabulary refusals, before any DB work ───────────────────────────────────
def test_bot_name_is_refused_on_the_person_settings_door(client, monkeypatch):
    """A bot default is a fact about the BOT. meetings owns it and resolves it on every spawn path
    through /internal/users/{id}/bot-context; accepting it here would be a second name for one
    fact. The refusal says where it lives."""
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    r = client.put("/internal/users/1/settings", json={"bot_name": "Notes"},
                   headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["refused"] == "bot_name is not a person setting"
    assert "bot-context" in detail["why"]


def test_a_non_object_body_is_refused(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    r = client.put("/internal/users/1/settings", json=["timezone"],
                   headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 422


# ── the pure decisions the two doors are wired to (unchanged, now reachable) ───────────────────
def test_apply_validates_every_key_before_writing_any():
    """All-or-nothing: a half-applied change is a person who believes they turned two things off
    and turned one."""
    before = {"person_settings": {"mail_minutes": True}}
    with pytest.raises(ps.Refused):
        ps.apply(before, {"mail_join": "off", "timezone": "Not/AZone"})
    assert before == {"person_settings": {"mail_minutes": True}}   # untouched

    after = ps.apply(before, {"mail_join": "off", "timezone": "Europe/Lisbon"})
    assert after["person_settings"] == {
        "mail_minutes": True, "mail_join": False, "timezone": "Europe/Lisbon"}


def test_the_five_person_facts_are_what_the_read_door_serves():
    facts = ps.read_person_facts({})
    assert set(facts) == {"timezone", "mail_minutes", "mail_join", "mail_rsvp", "mail_prep"}
    assert ps.BOT_NAME_KEY not in facts


def test_plan_import_keeps_an_already_set_key_and_drops_an_unknown_one():
    """Re-runnable across an estate where somebody has since changed a preference: a second run
    that clobbered it would silently undo a person's choice. And an unknown key is DROPPED, not
    refused — a migration that stops on one odd key leaves half the estate on the old store."""
    existing = {"person_settings": {"mail_minutes": False}}
    new, imported, kept, dropped = ps.plan_import(existing, {
        "mail_minutes": True,            # already set through the new door → kept
        "timezone": "Europe/Lisbon",     # → imported
        "colour": "blue",                # not in the vocabulary → dropped
    })
    assert imported == {"timezone": "Europe/Lisbon"}
    assert kept == ["mail_minutes"] and dropped == ["colour"]
    assert new["person_settings"]["mail_minutes"] is False


def test_plan_import_carries_bot_name_into_the_bots_own_store_only_when_empty():
    """The one path that carries `bot_name` — into `calendar_bot_name`, the store meetings already
    reads — and only when nobody has set one, in either direction."""
    new, imported, _kept, _dropped = ps.plan_import({}, {"bot_name": "Notes"})
    assert new[ps.BOT_NAME_STORE] == "Notes"
    assert imported == {"bot_name": "Notes"}

    new2, imported2, kept2, _ = ps.plan_import({ps.BOT_NAME_STORE: "Mine"}, {"bot_name": "Notes"})
    assert new2[ps.BOT_NAME_STORE] == "Mine"
    assert imported2 == {} and kept2 == ["bot_name"]
