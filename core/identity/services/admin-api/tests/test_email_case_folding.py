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


def test_creating_the_same_person_twice_in_two_cases_is_one_account(client):
    """THE GHOST, asserted absent. `ensure_platform_user` calls this route, so the create path is
    the one that actually mints the duplicate — the lookup only fails to prevent it."""
    first = client.post("/admin/users", headers=_admin(), json={"email": MIXED, "name": "Anna"})
    assert first.status_code == 201
    second = client.post("/admin/users", headers=_admin(), json={"email": LOWER, "name": "Anna"})
    assert second.status_code == 200, "a second case is not a second person"
    assert second.json()["id"] == first.json()["id"]


def test_a_new_address_is_stored_folded(client):
    """REVERSES an earlier decision here (`test_the_stored_address_is_not_rewritten`), which stored
    the typed case on the grounds that it is the address a person's mail is addressed to. Case in
    an address is not deliverability — every provider we meet folds the local part too — and
    keeping it cost the two things the read-side fold cannot buy (A20):

      * two concurrent creates in different cases BOTH miss the lookup and BOTH insert; there is no
        read fold that serialises them. Stored folded, the second collides with the `users.email`
        UNIQUE index that already exists (next test), so the race closes on a constraint;
      * `lower(email)` can never become UNIQUE while stored values disagree in case, so the index
        that would end this class stays non-unique for ever.

    The half of the old decision that survives is the next test: an address already in the table is
    never rewritten."""
    r = client.post("/admin/users", headers=_admin(), json={"email": MIXED})
    assert r.status_code == 201
    assert r.json()["email"] == LOWER
    assert client.get(f"/admin/users/email/{MIXED}", headers=_admin()).json()["email"] == LOWER


def test_an_address_already_stored_is_never_rewritten(pg_url, client):
    """New rows only. An existing row's case is the case its person typed, their mail already goes
    there, and rewriting the table to suit an index is changing data to suit a query plan."""
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(sa_text("INSERT INTO users (email, data) VALUES (:e, '{}'::jsonb)"),
                     {"e": MIXED})
    engine.dispose()

    # The same person signs in again, in another case: one account, and their stored address is
    # exactly what it was.
    r = client.post("/admin/users", headers=_admin(), json={"email": LOWER})
    assert r.status_code == 200
    assert r.json()["email"] == MIXED


def test_a_second_create_in_another_case_cannot_land_a_second_row(pg_url, client):
    """The concurrent-create race, forced deterministically: the row is planted BETWEEN the
    lookup and the insert by planting it first and then asking `create_user` to make it. Folded
    storage means the insert hits the `users.email` UNIQUE index, and the handler answers 200 on
    the existing row instead of 500."""
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(sa_text("INSERT INTO users (email, data) VALUES (:e, '{}'::jsonb)"),
                     {"e": LOWER})
        planted = conn.execute(sa_text("SELECT id FROM users WHERE email = :e"),
                               {"e": LOWER}).scalar_one()
    engine.dispose()

    r = client.post("/admin/users", headers=_admin(), json={"email": MIXED})
    assert r.status_code == 200
    assert r.json()["id"] == planted


def test_an_instance_that_already_holds_duplicates_resolves_the_same_row_every_time(pg_url, client):
    """ORDER BY id — OLDEST WINS, on BOTH lookups. Without it `.first()` returns whichever row the
    plan reached first: the two routes can disagree, and the same route can disagree with itself
    after a vacuum. Deterministic-wrong is recoverable; nondeterministic-wrong puts the desk on one
    account and the meetings on another."""
    from sqlalchemy import create_engine, text as sa_text

    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(sa_text("INSERT INTO users (email, data) VALUES (:e, '{}'::jsonb)"),
                     {"e": MIXED})           # the older row
        conn.execute(sa_text("INSERT INTO users (email, data) VALUES (:e, '{}'::jsonb)"),
                     {"e": LOWER})           # the ghost minted by the exact-match code
        oldest = conn.execute(sa_text(
            "SELECT min(id) FROM users WHERE lower(email) = :e"), {"e": LOWER}).scalar_one()
    engine.dispose()

    by_lookup = client.get(f"/admin/users/email/{LOWER}", headers=_admin()).json()["id"]
    by_create = client.post("/admin/users", headers=_admin(), json={"email": MIXED}).json()["id"]
    assert by_lookup == by_create == oldest


def test_two_different_people_are_still_two_accounts(client):
    a = client.post("/admin/users", headers=_admin(), json={"email": "a@acme.test"}).json()["id"]
    b = client.post("/admin/users", headers=_admin(), json={"email": "b@acme.test"}).json()["id"]
    assert a != b
    assert client.get("/admin/users/email/b@acme.test", headers=_admin()).json()["id"] == b
