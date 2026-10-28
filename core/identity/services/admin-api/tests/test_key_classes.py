"""MIGRATION-0014: a person's key list holds only the keys they made; the product's own credentials
are `system` keys, hidden, always ending (founder ruling 2026-10-23, #1868).

Customer key lists filled with hundreds of `key-NNNN` rows nobody made: the key each sign-in's cookie
carried, and the 15-minute keys flows minted for itself. These pin the admin door's side; the
convergence over a real database is in `test_key_classes_pg.py`.
"""
from datetime import datetime, timedelta

import pytest

from admin_api.key_store import SYSTEM_KEY_LIFETIME_SECONDS, ReservedKeyName, class_for_mint
from test_key_hashing import ADMIN, _mint, _validate, world  # noqa: F401 — the fixture is used by name


def _list(client, **q):
    return client.get("/admin/users/42/tokens", headers={"X-Admin-API-Key": ADMIN}, params=q).json()


def test_a_service_can_mint_a_system_key_and_it_ends(world):
    client, session = world
    r = _mint(client, name="sso", credential_class="system")
    assert r.status_code == 201, r.text
    row = session.tokens[r.json()["token"]]
    assert row.kind == "system"
    assert row.expires_at is not None
    assert abs((row.expires_at - datetime.utcnow()) - timedelta(seconds=SYSTEM_KEY_LIFETIME_SECONDS)) < timedelta(minutes=1)
    assert _validate(client, r.json()["token"]).status_code == 200


def test_a_system_key_never_outlives_a_cookie(world):
    client, session = world
    r = _mint(client, credential_class="system", expires_in=SYSTEM_KEY_LIFETIME_SECONDS * 10)
    assert session.tokens[r.json()["token"]].expires_at < datetime.utcnow() + timedelta(days=31)


def test_the_class_rides_the_query_too(world):
    client, session = world
    r = client.post("/admin/users/42/tokens?scopes=bot&name=sso&credential_class=system",
                    headers={"X-Admin-API-Key": ADMIN})
    assert r.status_code == 201, r.text
    assert session.tokens[r.json()["token"]].kind == "system"


@pytest.mark.parametrize("name", ["webapp-login", "terminal-login", "dashboard-login", "WebApp-Login"])
def test_a_sign_in_name_is_always_a_system_key_with_an_end(world, name):
    """A client that predates the class (an older webapp, the 0.10 dashboard image) still mints a
    hidden credential that ends."""
    client, session = world
    r = _mint(client, name=name)
    row = session.tokens[r.json()["token"]]
    assert row.kind == "system" and row.expires_at is not None


def test_the_admin_door_never_mints_a_session(world):
    client, session = world
    assert _mint(client, credential_class="session").status_code == 422
    assert client.post("/admin/users/42/tokens?scopes=bot&credential_class=session",
                       headers={"X-Admin-API-Key": ADMIN}).status_code == 422
    assert not session.tokens


def test_the_list_holds_only_the_persons_own_keys(world):
    client, _ = world
    _mint(client, name="my-script")
    _mint(client, name="mcp-connect")
    _mint(client, name="webapp-login")
    _mint(client, name="sso", credential_class="system")
    assert [k["name"] for k in _list(client)] == ["my-script", "mcp-connect"]
    assert {k["credential_class"] for k in _list(client)} == {"key"}
    everything = _list(client, include_hidden="true")
    assert [(k["name"], k["credential_class"]) for k in everything] == [
        ("my-script", "key"), ("mcp-connect", "key"), ("webapp-login", "system"), ("sso", "system")]


def test_a_person_key_keeps_the_lifetime_it_was_given(world):
    client, session = world
    forever = session.tokens[_mint(client, name="ci").json()["token"]]
    assert forever.kind == "key" and forever.expires_at is None


@pytest.mark.parametrize("requested,name,kind", [
    (None, None, "key"), ("key", "x", "key"), ("system", "x", "system"),
    (None, "webapp-login", "system"), ("system", " terminal-login ", "system"), (None, "mcp-connect", "key"),
])
def test_class_for_mint(requested, name, kind):
    assert class_for_mint(requested, name) == kind


@pytest.mark.parametrize("name", ["terminal-login", " WebApp-Login ", "dashboard-login"])
def test_a_persons_key_under_a_sign_in_name_is_refused_not_hidden(world, name):
    client, session = world
    with pytest.raises(ReservedKeyName):
        class_for_mint("key", name)
    r = _mint(client, name=name, credential_class="key")
    assert r.status_code == 422 and "reserved" in r.text
    assert not session.tokens
