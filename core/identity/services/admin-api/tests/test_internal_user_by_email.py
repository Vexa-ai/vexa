"""The narrowest door in admin-api: an address → a subject id, and nothing else.

The post-meeting run mounts the desks of the people who were in the meeting, starting from the
invite's ATTENDEE addresses, so agent-api has to turn an address into a subject. Before this route
it had two options and both were wrong: borrow `verify_admin_token` (a credential that can also
create and patch users — a permanent over-grant for one read-only question), or infer the subject
from a speaker's display NAME, which mounts the wrong human's desk. This route exists so neither
is necessary.

What these tests pin is mostly what the response does NOT contain. A route that answers exactly
one question cannot be repurposed into a directory; one that also returned `email`, `name` or
`data` could be, and nobody would notice the day it started being used that way.
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


def test_a_known_address_resolves_to_its_id_and_to_NOTHING_else(client):
    uid = client.post("/admin/users", headers=_admin(),
                      json={"email": "attendee@vexa.ai", "name": "An Attendee"}).json()["id"]

    r = client.get("/internal/users/by-email/attendee@vexa.ai", headers=_internal())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"id": uid}, body
    # Stated separately from the equality above, because THIS is the assertion with a reason: the
    # caller already knows the address it asked about, so every other field would be a disclosure
    # this question does not need. The equality could be loosened by accident; this cannot.
    for leaked in ("email", "name", "data", "scopes", "created_at", "max_concurrent_bots"):
        assert leaked not in body


def test_an_unknown_address_is_404_and_not_an_empty_success(client):
    # "No subject yet" is a real answer the mount path acts on — it skips that desk, and the drop
    # step creates it afterwards. A 200 with a null id would make "absent" indistinguishable from
    # "resolved to nothing", which is how a room silently mounts the wrong set.
    r = client.get("/internal/users/by-email/nobody@vexa.ai", headers=_internal())
    assert r.status_code == 404


def test_without_the_internal_secret_it_answers_nothing(client):
    # The whole point of the route is that it needs no admin token. That is only safe while the
    # internal tier is actually enforced on it.
    assert client.get("/internal/users/by-email/attendee@vexa.ai").status_code == 403
    assert client.get("/internal/users/by-email/attendee@vexa.ai",
                      headers={"X-Internal-Secret": "wrong"}).status_code == 403


def test_the_admin_token_is_NOT_a_way_in(client):
    # Not because an admin may not know a user id, but because the tiers must stay distinct: this
    # route is the internal tier's, and a second accepted credential is a second thing to reason
    # about the day either one is rotated.
    client.post("/admin/users", headers=_admin(), json={"email": "attendee@vexa.ai"})
    assert client.get("/internal/users/by-email/attendee@vexa.ai",
                      headers=_admin()).status_code == 403


def test_the_literal_path_does_not_shadow_the_id_routes(client):
    # `/internal/users/by-email/{email}` and `/internal/users/{user_id}/is-admin` are the same
    # SHAPE. Registration order decides, and a future edit that moves one above the other would
    # break the other silently — so both are exercised here together.
    uid = client.post("/admin/users", headers=_admin(),
                      json={"email": "both@vexa.ai"}).json()["id"]
    assert client.get("/internal/users/by-email/both@vexa.ai",
                      headers=_internal()).json() == {"id": uid}
    assert client.get(f"/internal/users/{uid}/is-admin",
                      headers=_internal()).status_code == 200
