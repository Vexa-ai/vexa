"""D2 (0.13.4): the workspace principal — a room's turn as the workspace's agent, never a person.

identity keeps one numeric principal of kind `workspace` per workspace (`principals`, its id a
dedicated account row). A delegation token naming it is answered as that account — which owns
nothing of a person's — with `principal_kind`, `principal_workspace`, `on_behalf_of` and `bills_to`
on `delegation`; the claims must agree with the table. BL-3: a shared or workspace token reaches no
person settings door. No database: a fake session that knows users and principals.
"""
import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import Principal, User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"
DECLARED = {validate_mod.ACCEPTS_DELEGATION_HEADER: validate_mod.ACCEPTS_DELEGATION_VALUE}


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _Session:
    def __init__(self, users, principals):
        self.users, self.principals, self.added = users, principals, []

    async def execute(self, stmt):
        table = stmt.column_descriptions[0]["entity"]
        params = stmt.compile().params
        value = next(iter(params.values()), None)
        if table is Principal:
            if isinstance(value, int):
                return _Result(self.principals.get(value))
            return _Result(next((p for p in self.principals.values() if p.workspace_id == value), None))
        return _Result(self.users.get(value) if isinstance(value, int) else None)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, User):
            obj.id = 2000 + len(self.added)
            self.users[obj.id] = obj
        if isinstance(obj, Principal):
            self.principals[obj.id] = obj

    async def flush(self):
        pass

    async def commit(self):
        pass


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("VEXA_FEATURES", "all,public_profiles")
    owner = User(id=42, email="ada@example.com", max_concurrent_bots=2,
                 data={"is_admin": True, "memberships": [{"workspace_id": "ws_1", "role": "owner"},
                                                         {"workspace_id": "board", "role": "owner"}]})
    ws_account = User(id=1042, email="ws-1042@principal.invalid", max_concurrent_bots=0,
                      data={"principal_kind": "workspace", "workspace_id": "ws_1"})
    principals = {1042: Principal(id=1042, kind="workspace", workspace_id="ws_1", bills_to=42, status="active")}
    session = _Session({42: owner, 1042: ws_account}, principals)
    app = create_app()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app), session


def _token(**kw):
    args = dict(subject="1042", regime="human", workspaces=["ws_1"], shared=True,
                principal_kind="workspace", principal_workspace="ws_1", on_behalf_of="43",
                unit="agent-ws-ws_1-room-s1")
    args.update(kw)
    return delegation.mint_delegation(DLG, **args)


def _validate(client, token):
    return client.post("/internal/validate", json={"token": token},
                       headers={"X-Internal-Secret": INTERNAL, **DECLARED})


def test_a_workspace_token_is_answered_as_the_principal_with_its_payer(world):
    client, _ = world
    body = _validate(client, _token()).json()
    assert body["user_id"] == 1042, "the principal's own account, never the owner's"
    assert body["delegation"] == {"regime": "human", "workspaces": ["ws_1"], "shared": True,
                                  "unit": "agent-ws-ws_1-room-s1", "principal_kind": "workspace",
                                  "principal_workspace": "ws_1", "on_behalf_of": "43", "bills_to": 42}
    assert body["workspaces"] == ["ws_1"] and body["writable_workspaces"] == ["ws_1"]
    assert body["is_admin"] is False and body["person_is_admin"] is False
    assert "board" not in body["workspaces"], "none of the payer's other workspaces"


@pytest.mark.parametrize("kw", [{"principal_workspace": "board", "workspaces": ["board"]},  # not its workspace
                                {"subject": "42"}])                                           # a person's id
def test_claims_that_disagree_with_the_table_are_refused(world, kw):
    client, _ = world
    r = _validate(client, _token(**kw))
    assert r.status_code == 401 and "principal" in r.text


def test_a_disabled_principal_is_refused(world):
    client, session = world
    session.principals[1042].status = "disabled"
    assert _validate(client, _token()).status_code == 401


def test_the_internal_door_makes_one_principal_per_workspace_and_moves_its_payer(world):
    client, session = world
    h = {"X-Internal-Secret": INTERNAL}
    first = client.post("/internal/principals/workspace", json={"workspace_id": "team", "bills_to": 42}, headers=h)
    assert first.status_code == 200, first.text
    p = first.json()
    assert p["kind"] == "workspace" and p["workspace_id"] == "team" and p["bills_to"] == 42
    assert session.users[p["id"]].email.endswith("@principal.invalid")
    again = client.post("/internal/principals/workspace", json={"workspace_id": "team", "bills_to": 42}, headers=h)
    assert again.json()["id"] == p["id"]
    assert client.post("/internal/principals/workspace", json={"workspace_id": "x", "bills_to": 1042},
                       headers=h).status_code == 422, "a principal never pays for another"
    assert client.post("/internal/principals/workspace", json={"workspace_id": "x", "bills_to": 42}).status_code in (401, 403)


@pytest.mark.parametrize("path", ["/user/settings", "/user/workspace-regime", "/user/profile"])
def test_bl3_no_shared_or_workspace_token_reads_a_persons_settings(world, path):
    client, _ = world
    shared = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces=["ws_1"], shared=True)
    for tok in (shared, _token()):
        r = client.get(path, headers={"X-API-Key": tok})
        assert r.status_code == 403 and "shared_conversation" in r.text, (path, r.status_code, r.text[:200])
