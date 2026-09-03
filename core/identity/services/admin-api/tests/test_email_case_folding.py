"""AN ADDRESS IS ONE ACCOUNT, whatever case it was typed in — R-B08.

This service already knew that. Sign-in resolves with `func.lower(User.email) == email`; the three
routes below matched `User.email == email` exactly. The consequence is not a failed lookup, which
would be loud — it is a SECOND ACCOUNT, and the flows engine mints it automatically:

    agent-api lowercases before resolving        → the room cannot see `Anna.Smith@acme.com`
    admin-api matches exactly                    → `GET /admin/users/email/...` says "no such user"
    `ensure_platform_user` in `drop_to_attendees` → creates one

The ghost has an empty desk, and it is the ghost that receives the meeting report while the real
account gets nothing. Every test here fails on `origin/minutes-mcp-viewer` @ b25733d12.
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

MIXED = "Anna.Smith@acme.test"
LOWER = "anna.smith@acme.test"


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


def test_a_mixed_case_signup_resolves_through_the_admin_lookup(client):
    """The asking half. Flows asks here, is told "no such user", and creates one."""
    uid = client.post("/admin/users", headers=_admin(),
                      json={"email": MIXED, "name": "Anna"}).json()["id"]
    r = client.get(f"/admin/users/email/{LOWER}", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json()["id"] == uid


def test_it_resolves_through_the_internal_by_email_route_too(client):
    """The MOUNT path reads this one, so an exact match here silently drops a mixed-case signup
    out of every meeting room they are actually in."""
    uid = client.post("/admin/users", headers=_admin(), json={"email": MIXED}).json()["id"]
    r = client.get(f"/internal/users/by-email/{LOWER}", headers=_internal())
    assert r.status_code == 200 and r.json() == {"id": uid}


def test_creating_the_same_person_twice_in_two_cases_is_one_account(client):
    """THE GHOST, asserted absent. `ensure_platform_user` calls this route, so the create path is
    the one that actually mints the duplicate — the lookup only fails to prevent it."""
    first = client.post("/admin/users", headers=_admin(), json={"email": MIXED, "name": "Anna"})
    assert first.status_code == 201
    second = client.post("/admin/users", headers=_admin(), json={"email": LOWER, "name": "Anna"})
    assert second.status_code == 200, "a second case is not a second person"
    assert second.json()["id"] == first.json()["id"]


def test_the_stored_address_is_not_rewritten(client):
    """Case-INSENSITIVE matching, not case-DESTROYING storage: the address a person typed is the
    address we show them and the one their mail is addressed to."""
    client.post("/admin/users", headers=_admin(), json={"email": MIXED})
    assert client.get(f"/admin/users/email/{MIXED}", headers=_admin()).json()["email"] == MIXED


def test_two_different_people_are_still_two_accounts(client):
    a = client.post("/admin/users", headers=_admin(), json={"email": "a@acme.test"}).json()["id"]
    b = client.post("/admin/users", headers=_admin(), json={"email": "b@acme.test"}).json()["id"]
    assert a != b
    assert client.get("/admin/users/email/b@acme.test", headers=_admin()).json()["id"] == b
