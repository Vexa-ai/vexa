"""`presence` — the person's choice to be seen as active by colleagues, owned by identity (#1783).

The desktop and the home's people strip show a coarse dot ("now" / "today") for a colleague. A
person may switch theirs off, and that choice wins over every policy. It used to live in the home's
event log inside agent-api; it is a fact about the PERSON, so identity keeps it beside the other
person settings, and agent-api reads it per request.

Three properties:

  * the person changes it with their OWN credential only. A worker acting for them — even one with
    the person in the loop — may read it and may not change it: whether colleagues see you is not
    something an agent decides on your behalf.
  * agent-api reads it for many people at once (the members of a desktop) through one internal
    door, which answers ``null`` for "never chosen" so a one-time carry-over from the old home
    event can tell "never chose" from "chose to be seen".
  * that door needs the internal secret with no dev-mode bypass: it names other people.

Offline: the DB session is a stub; no Postgres, no docker.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import delegation_revocation as revocation
from admin_api.app import person_settings as ps
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

DLG = "delegation-secret-for-the-presence-test"
SECRET = "internal-secret-for-the-presence-test"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._rows[0]

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, users):
        self._users = users

    async def execute(self, stmt):
        ids: list = []
        for v in stmt.compile().params.values():
            if isinstance(v, int):
                ids.append(v)
            elif isinstance(v, (list, tuple)):
                ids.extend(i for i in v if isinstance(i, int))
        return _Result([self._users[i] for i in ids if i in self._users])

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture()
def people():
    return {
        47: User(id=47, email="a@example.test", data={}),
        48: User(id=48, email="b@example.test", data={ps.DATA_KEY: {"presence": False}}),
        49: User(id=49, email="c@example.test", data={ps.DATA_KEY: {"presence": True}}),
    }


@pytest.fixture()
def client(monkeypatch, people):
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-presence-test")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")

    async def _admitted(jti):
        return True

    monkeypatch.setattr(revocation, "is_admitted", _admitted)
    app = create_app()

    async def _db():
        yield _Session(people)

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c


def _own_key(client, monkeypatch, user):
    """The person's own API key: the token-table lookup, stubbed to answer this person."""
    from admin_api.app import main as main_mod

    async def _me():
        return user

    for dep in (main_mod.get_current_user, main_mod.get_current_user_for_update,
                main_mod.get_current_person, main_mod.get_current_person_for_update):
        client.app.dependency_overrides[dep] = _me
    return {"X-API-Key": "vxa_the-persons-own-key"}


def _worker(regime="human", subject="47"):
    return {"X-API-Key": delegation.mint_delegation(DLG, subject=subject, regime=regime,
                                                     workspaces="*" if regime == "human" else [subject],
                                                     unit=f"agent-{subject}-chat-main")}


# ── the vocabulary ──────────────────────────────────────────────────────────────────────────────

def test_presence_is_a_person_setting_on_by_default():
    assert ps.read({})["presence"] is True
    assert ps.apply({}, {"presence": "off"})[ps.DATA_KEY]["presence"] is False
    assert "presence" in ps.MEANINGS and ps.MEANINGS["presence"]


def test_presence_choice_tells_never_chosen_from_chosen():
    assert ps.presence_choice({}) is None
    assert ps.presence_choice({ps.DATA_KEY: {"mail_minutes": False}}) is None
    assert ps.presence_choice({ps.DATA_KEY: {"presence": False}}) is False
    assert ps.presence_choice({ps.DATA_KEY: {"presence": True}}) is True


# ── set and read with the person's own credential ───────────────────────────────────────────────

def test_the_person_switches_presence_off_with_their_own_key(client, monkeypatch, people):
    h = _own_key(client, monkeypatch, people[47])
    assert client.get("/user/settings", headers=h).json()["settings"]["presence"] is True
    r = client.put("/user/settings", json={"setting": "presence", "value": "off"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == {"presence": False}
    assert people[47].data[ps.DATA_KEY]["presence"] is False


def test_a_worker_reads_presence_but_never_changes_it_even_with_the_person_in_the_loop(client, people):
    assert client.get("/user/settings", headers=_worker("human")).json()["settings"]["presence"] is True
    for regime in ("human", "autonomous"):
        r = client.put("/user/settings", json={"setting": "presence", "value": "off"}, headers=_worker(regime))
        assert r.status_code == 403, (regime, r.text)
    assert ps.DATA_KEY not in (people[47].data or {})
    # the person's other settings are unchanged by this: a watched worker still sets those
    r = client.put("/user/settings", json={"setting": "mail_minutes", "value": "off"}, headers=_worker("human"))
    assert r.status_code == 200, r.text


# ── the internal batch read agent-api uses ──────────────────────────────────────────────────────

def test_the_internal_door_answers_many_people_at_once_with_null_for_never_chosen(client):
    r = client.get("/internal/presence", params={"ids": "47,48,49,999"}, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200, r.text
    assert r.json() == {"presence": {"47": None, "48": False, "49": True}}, \
        "an unknown id is absent, never defaulted: the caller treats absence as hidden"


def test_the_internal_door_ignores_ids_that_are_not_ids(client):
    r = client.get("/internal/presence", params={"ids": "47,u_x,,48"}, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200, r.text
    assert r.json() == {"presence": {"47": None, "48": False}}


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Secret": "wrong"}])
def test_the_internal_door_needs_the_secret(client, headers):
    assert client.get("/internal/presence", params={"ids": "48"}, headers=headers).status_code in (401, 403)


def test_the_internal_door_has_no_dev_mode_bypass(client, monkeypatch):
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    assert client.get("/internal/presence", params={"ids": "48"}).status_code in (401, 403, 503)


def test_the_internal_door_caps_one_read(client):
    ids = ",".join(str(i) for i in range(1, 300))
    r = client.get("/internal/presence", params={"ids": ids}, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 422, r.text
