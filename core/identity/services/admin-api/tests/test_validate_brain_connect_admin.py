"""/internal/validate refuses a `brain-connect` key whose person is an admin (card 7, design rev 9).

`POST /connect` (app/connect.py) refuses an admin when it issues the key. A person can still become an
admin afterwards, by any of the three sources identity decides admin from: `users.data.is_admin`,
`VEXA_ADMIN_EMAILS`, the admin-account stamp. So the token check refuses a key named
`connect.KEY_NAME` for such a person with 401, early, whenever the person became one. Nothing else
moves: their other keys, and a non-admin's `brain-connect` key, validate exactly as before. The gateway
asks this check on every request (no cache), so its answer is the gateway's for every keyed route.
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text

from admin_api.app import admin_account
from admin_api.app import connect as connect_mod

from conftest import requires_docker
from test_connect import EMAIL, _admin, client, connect, counter, id_token  # noqa: F401 — fixtures
from test_stack_admin_api import INTERNAL_SECRET

pytestmark = requires_docker


def _exec(pg_url, sql, **params):
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


def _validate(client, key):
    return client.post("/internal/validate", headers={"X-Internal-Secret": INTERNAL_SECRET}, json={"token": key})


def _connected(client):
    r = connect(client)
    assert r.status_code == 200, r.text
    return r.json()["user_id"], r.json()["key"]


def _other_key(client, uid, name="my laptop"):
    r = client.post(f"/admin/users/{uid}/tokens", headers=_admin(), json={"scopes": ["bot", "tx"], "name": name})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def test_a_non_admins_brain_connect_key_validates_as_before(client):
    uid, key = _connected(client)
    v = _validate(client, key)
    assert v.status_code == 200 and v.json()["user_id"] == uid and v.json()["is_admin"] is False


def test_the_admin_role_refuses_the_brain_connect_key_only(client, pg_url):
    uid, key = _connected(client)
    other = _other_key(client, uid)
    _exec(pg_url, "UPDATE users SET data = data || '{\"is_admin\": true}'::jsonb WHERE id = :u", u=uid)
    assert _validate(client, key).status_code == 401
    v = _validate(client, other)
    assert v.status_code == 200 and v.json()["is_admin"] is True, "the admin's other keys are unchanged"


def test_an_admin_named_by_the_deployment_has_the_brain_connect_key_refused(client, monkeypatch):
    uid, key = _connected(client)
    other = _other_key(client, uid)
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", EMAIL)
    assert _validate(client, key).status_code == 401
    assert _validate(client, other).status_code == 200
    monkeypatch.delenv("VEXA_ADMIN_EMAILS")
    assert _validate(client, key).status_code == 200, "decided per request, from the person as they are now"


def test_the_admin_account_stamp_refuses_the_brain_connect_key(client, pg_url):
    uid, key = _connected(client)
    other = _other_key(client, uid)
    stamp = json.dumps({admin_account.ACCOUNT_FIELD: admin_account.ACCOUNT_ADMIN})
    _exec(pg_url, "UPDATE users SET data = data || CAST(:s AS jsonb) WHERE id = :u", s=stamp, u=uid)
    assert _validate(client, key).status_code == 401
    assert _validate(client, other).status_code == 200


def test_a_key_a_person_names_brain_connect_themselves_only_refuses_more(client, pg_url):
    """The terminal's token surface lets a person name a key anything not reserved. A key they name
    `brain-connect` is held to the same rule: refused while they are an admin, unchanged otherwise."""
    ordinary = client.post("/admin/users", headers=_admin(), json={"email": "bo@corp.example"}).json()["id"]
    own = _other_key(client, ordinary, name=connect_mod.KEY_NAME)
    assert _validate(client, own).status_code == 200
    admin = client.post("/admin/users", headers=_admin(), json={"email": "cy@corp.example"}).json()["id"]
    _exec(pg_url, "UPDATE users SET data = data || '{\"is_admin\": true}'::jsonb WHERE id = :u", u=admin)
    named = _other_key(client, admin, name=connect_mod.KEY_NAME)
    assert _validate(client, named).status_code == 401
    assert _validate(client, _other_key(client, admin, name="Brain-Connect")).status_code == 200, \
        "the exact name only; any other name validates as before"
