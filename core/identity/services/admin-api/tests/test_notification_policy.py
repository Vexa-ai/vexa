"""The person's NOTIFICATION POLICY (PRD 0.13.4 S6, D15): identity owns it, Settings → Notifications
writes it through `/user/notifications`, and flows' delivery gate reads it on the internal settings
door it already calls. Offline: the DB session and the caller are stubbed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api.app import db as app_db
from admin_api.app import main as main_mod
from admin_api.app import notification_policy as np
from admin_api.app.main import create_app
from admin_api.schema.models import User


# ── the vocabulary ──────────────────────────────────────────────────────────────────────────────

def test_an_empty_person_reads_the_defaults():
    assert np.read({}) == np.DEFAULT
    assert np.read({"person_settings": {"notifications": "garbage"}}) == np.DEFAULT


def test_a_change_merges_nested_objects_and_keeps_the_rest():
    data = np.apply({}, {"quiet": {"from": "23:00"}, "rooms": {"room:s1": "none", "ws:growth": "mentions"}})
    pol = np.read(data)
    assert pol["quiet"] == {"on": True, "from": "23:00", "to": "08:00"}
    assert pol["rooms"] == {"room:s1": "none", "ws:growth": "mentions"}
    data = np.apply(data, {"rooms": {"room:s1": None}, "budget": 4})
    assert np.read(data)["rooms"] == {"ws:growth": "mentions"} and np.read(data)["budget"] == 4


@pytest.mark.parametrize("patch", [
    {"budget": 0}, {"budget": 51}, {"budget": "6"}, {"idle_min": True}, {"digest": "weekly"},
    {"digest_at": "25:00"}, {"quiet": {"from": "10pm"}}, {"rooms": {"#general": "none"}},
    {"rooms": {"room:s1": "loud"}}, {"sounds": {"volume": 2}}, {"nope": 1}, {},
    {"channels": {"sms": True}},
])
def test_a_bad_value_refuses_the_whole_change(patch):
    with pytest.raises(np.Refused):
        np.apply({}, patch)


def test_whatsapp_is_refused_until_it_ships():
    with pytest.raises(np.Refused) as e:
        np.apply({}, {"channels": {"whatsapp": True}})
    assert "later release" in e.value.detail["refused"]
    assert np.read(np.apply({}, {"channels": {"whatsapp": False, "email": False}}))["channels"] == {
        "email": False, "whatsapp": False}


def test_all_or_nothing():
    with pytest.raises(np.Refused):
        np.apply({}, {"budget": 4, "digest": "weekly"})


# ── the doors ───────────────────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def scalar_one(self):
        return self._user


class _Session:
    user = None

    async def execute(self, stmt):
        return _Result(self.user)

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture()
def person():
    return User(id=7, email="v@example.test", data={"person_settings": {"timezone": "Europe/Lisbon"}})


@pytest.fixture()
def client(monkeypatch, person):
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-notification-test")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret-for-the-notification-test")
    app = create_app()

    async def _db():
        session = _Session()
        session.user = person
        yield session

    async def _me():
        return person

    app.dependency_overrides[app_db.get_db] = _db
    for dep in (main_mod.get_current_user, main_mod.get_current_user_for_update,
                main_mod.get_current_person, main_mod.get_current_person_for_update):
        app.dependency_overrides[dep] = _me
    with TestClient(app) as c:
        yield c


def test_the_page_reads_the_policy_its_clock_and_the_global_limits(client):
    r = client.get("/user/notifications")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notifications"] == np.DEFAULT and body["timezone"] == "Europe/Lisbon"
    assert body["limits"] == {"idle_floor_min": 5, "budget_ceiling": 12}


def test_the_person_changes_it_and_a_bad_value_changes_nothing(client, person):
    r = client.put("/user/notifications", json={"idle_min": 15, "digest": "daily", "digest_at": "17:30"})
    assert r.status_code == 200, r.text
    assert r.json()["notifications"]["idle_min"] == 15
    stored = person.data["person_settings"]["notifications"]
    assert stored["digest"] == "daily" and stored["digest_at"] == "17:30"
    assert person.data["person_settings"]["timezone"] == "Europe/Lisbon", "other settings untouched"
    r = client.put("/user/notifications", json={"idle_min": 15, "budget": 999})
    assert r.status_code == 422
    assert person.data["person_settings"]["notifications"]["budget"] == 6


def test_flows_reads_it_on_the_internal_settings_door(client, person):
    person.data = np.apply(person.data, {"budget": 4})
    r = client.get("/internal/users/7/settings",
                   headers={"X-Internal-Secret": "internal-secret-for-the-notification-test"})
    assert r.status_code == 200, r.text
    assert r.json()["notifications"]["budget"] == 4 and r.json()["timezone"] == "Europe/Lisbon"
