"""The account mode (app/account_mode.py): `claude` is stamped on a new row only, an operator moves it
either way, and the internal role door answers it."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api.app import account_mode
from admin_api.app import main as main_mod
from admin_api.app.db import get_db
from admin_api.app.main import UserAdminPatch, UserCreate, create_app
from admin_api.schema.models import User


def test_absent_and_junk_read_standard():
    assert account_mode.of(None) == "standard"
    assert account_mode.of({}) == "standard"
    assert account_mode.of({"account_mode": "CLAUDE"}) == "standard"
    assert account_mode.of({"account_mode": "claude"}) == "claude"
    assert account_mode.agents_off({"account_mode": "claude"}) is True
    assert account_mode.agents_off({}) is False


def test_apply_stores_claude_and_standard_removes_it():
    data = account_mode.apply({"x": 1}, "claude")
    assert data == {"x": 1, "account_mode": "claude"}
    assert account_mode.apply(data, "standard") == {"x": 1}
    with pytest.raises(ValueError):
        account_mode.apply({}, "robot")


def test_create_accepts_only_claude_and_patch_accepts_both():
    assert UserCreate(email="a@b.c", account_mode="claude").account_mode == "claude"
    with pytest.raises(ValueError):
        UserCreate(email="a@b.c", account_mode="standard")
    assert UserAdminPatch(account_mode="standard").account_mode == "standard"
    with pytest.raises(ValueError):
        UserAdminPatch()


class _Result:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._row


class _Session:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    async def execute(self, *_a, **_k):
        return _Result(self.existing)

    async def get(self, _model, _id):
        return self.existing

    def add(self, row):
        row.id = row.id or 501
        self.added.append(row)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def refresh(self, _row):
        pass


def _client(monkeypatch, session):
    monkeypatch.setenv("ADMIN_API_TOKEN", "adm")
    monkeypatch.setenv("INTERNAL_API_SECRET", "int")
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_ACCOUNT", raising=False)

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(main_mod.events_mod, "publish", _noop)
    app = create_app()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_a_new_row_is_stamped_claude_when_the_door_asks(monkeypatch):
    session = _Session()
    r = _client(monkeypatch, session).post(
        "/admin/users", json={"email": "New@Example.com", "account_mode": "claude"},
        headers={"X-Admin-API-Key": "adm"})
    assert r.status_code == 201, r.text
    assert session.added[0].data["account_mode"] == "claude"
    assert r.json()["data"]["account_mode"] == "claude"


def test_an_existing_account_is_never_moved_by_a_sign_in(monkeypatch):
    existing = User(id=7, email="old@example.com", max_concurrent_bots=3, data={"is_admin": True})
    session = _Session(existing)
    r = _client(monkeypatch, session).post(
        "/admin/users", json={"email": "old@example.com", "account_mode": "claude"},
        headers={"X-Admin-API-Key": "adm"})
    assert r.status_code == 200, r.text
    assert "account_mode" not in existing.data
    assert session.added == []


def test_the_operator_moves_an_account_and_the_role_door_answers_it(monkeypatch):
    user = User(id=8, email="t@example.com", max_concurrent_bots=3, data={"x": 1})
    client = _client(monkeypatch, _Session(user))
    r = client.patch("/admin/users/8", json={"account_mode": "claude"}, headers={"X-Admin-API-Key": "adm"})
    assert r.status_code == 200, r.text
    assert user.data == {"x": 1, "account_mode": "claude"}
    door = client.get("/internal/users/8/is-admin", headers={"X-Internal-Secret": "int"})
    assert door.status_code == 200, door.text
    assert door.json()["account_mode"] == "claude"
    client.patch("/admin/users/8", json={"account_mode": "standard"}, headers={"X-Admin-API-Key": "adm"})
    assert user.data == {"x": 1}
