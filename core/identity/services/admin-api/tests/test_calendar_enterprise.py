"""Enterprise calendar settings on the identity side.

* ``VEXA_CALENDAR_FEED_ALLOW`` — an operator's internal feed hosts/networks: a feed inside them may
  be saved, everything else internal stays refused (pure; no DB).
* ``auto_join_block`` — the owner's list of domains/addresses the calendar bot must never
  auto-join: validated on ``PUT /user/calendar``, read back, and stated on bot-context, where
  meeting-api's sweep enforces it (testcontainers PG; skips without docker).
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.calendars import feed_allowance, validate_auto_join_block, validate_ics_url
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

ALLOW = "cal.corp.example, 10.20.0.0/16"


@pytest.mark.parametrize("url", ["https://10.20.1.1/team.ics", "https://calendar/team.ics"])
def test_without_an_allowance_an_internal_feed_cannot_be_saved(url, monkeypatch):
    # A dotted name (cal.corp.example) is not resolved at save time; the fetch refuses it.
    monkeypatch.delenv("VEXA_CALENDAR_FEED_ALLOW", raising=False)
    with pytest.raises(HTTPException) as exc:
        validate_ics_url(url)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("url", ["https://10.20.1.1/team.ics", "https://calendar/team.ics"])
def test_an_operator_allowed_internal_feed_can_be_saved(url, monkeypatch):
    monkeypatch.setenv("VEXA_CALENDAR_FEED_ALLOW", ALLOW + " calendar")
    assert validate_ics_url(url) == url


@pytest.mark.parametrize("url", ["https://10.21.0.1/x.ics", "https://127.0.0.1/x.ics",
                                 "https://169.254.169.254/", "https://admin-api:8001/",
                                 "https://localhost/x.ics", "https://[::1]/x.ics"])
def test_with_an_allowance_everything_else_internal_is_still_refused(url, monkeypatch):
    monkeypatch.setenv("VEXA_CALENDAR_FEED_ALLOW", ALLOW)
    with pytest.raises(HTTPException) as exc:
        validate_ics_url(url)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("bad", ["0.0.0.0/0", "169.254.0.0/16", "*.corp.example", "localhost"])
def test_an_unsafe_allowance_entry_is_refused(bad):
    with pytest.raises(ValueError, match="VEXA_CALENDAR_FEED_ALLOW"):
        feed_allowance(bad)


def test_the_block_list_normalizes_and_refuses_junk():
    assert validate_auto_join_block(["Gated.Example", "@gated.example", "*.other.example",
                                     "CEO@Partner.Example"]) == [
        "gated.example", "other.example", "ceo@partner.example"]
    for bad in (["localhost"], ["http://x.example"], ["a@b"], "gated.example", [3]):
        with pytest.raises(HTTPException):
            validate_auto_join_block(bad)
    with pytest.raises(HTTPException):
        validate_auto_join_block([f"d{i}.example" for i in range(201)])


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


@requires_docker
def test_the_owner_block_list_round_trips_and_reaches_bot_context(client):
    uid = client.post("/admin/users", headers=_admin(), json={"email": "block@vexa.ai"}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    h = {"X-API-Key": tok}
    internal = {"X-Internal-Secret": INTERNAL_SECRET}

    assert client.get(f"/internal/users/{uid}/bot-context", headers=internal).json()["auto_join_block"] == []
    r = client.put("/user/calendar", headers=h, json={"auto_join_block": ["Gated.Example", "ceo@partner.example"]})
    assert r.status_code == 200, r.text
    assert r.json()["auto_join_block"] == ["gated.example", "ceo@partner.example"]
    assert client.get("/user/calendar", headers=h).json()["auto_join_block"] == ["gated.example", "ceo@partner.example"]
    assert client.get(f"/internal/users/{uid}/bot-context", headers=internal).json()["auto_join_block"] == [
        "gated.example", "ceo@partner.example"]

    # Other calendar fields leave the list alone; junk is refused and changes nothing; [] clears.
    client.put("/user/calendar", headers=h, json={"bot_name": "Notes"})
    assert client.get("/user/calendar", headers=h).json()["auto_join_block"] == ["gated.example", "ceo@partner.example"]
    assert client.put("/user/calendar", headers=h, json={"auto_join_block": ["not a domain"]}).status_code == 422
    assert client.get("/user/calendar", headers=h).json()["auto_join_block"] == ["gated.example", "ceo@partner.example"]
    client.put("/user/calendar", headers=h, json={"auto_join_block": []})
    assert client.get(f"/internal/users/{uid}/bot-context", headers=internal).json()["auto_join_block"] == []


@requires_docker
def test_one_owner_cannot_see_or_change_another_owners_block_list(client):
    a = client.post("/admin/users", headers=_admin(), json={"email": "a-block@vexa.ai"}).json()["id"]
    b = client.post("/admin/users", headers=_admin(), json={"email": "b-block@vexa.ai"}).json()["id"]
    ta = client.post(f"/admin/users/{a}/tokens?scopes=bot", headers=_admin()).json()["token"]
    client.put("/user/calendar", headers={"X-API-Key": ta}, json={"auto_join_block": ["gated.example"]})
    internal = {"X-Internal-Secret": INTERNAL_SECRET}
    assert client.get(f"/internal/users/{b}/bot-context", headers=internal).json()["auto_join_block"] == []
    # bot-context is internal-tier only
    assert client.get(f"/internal/users/{a}/bot-context", headers={"X-API-Key": ta}).status_code in (401, 403)
