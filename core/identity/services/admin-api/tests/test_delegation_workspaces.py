"""A worker's identity carries only the person's memberships inside its dispatch's ceiling.

`/internal/validate` answers a delegation token with the person it acts for. The services behind the
gateway read that answer's `workspaces` as the shared workspaces the bearer reads through —
meeting-api grants a read of a meeting bound to any of them — so for a worker it is the person's
memberships narrowed to the ceiling, by the delegation contract's one rule (`ceiling_reads`, the
rule agent-api's resolvers apply). A person in the loop (`"*"`) keeps every membership; an API key
is not narrowed at all; narrowing never adds a workspace the person is not a member of.

No database: `get_db` is overridden with a session that knows one user.
"""
import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

INTERNAL = "internal-secret-for-tests"
DLG = "delegation-secret-for-tests"
MEMBERSHIPS = ["ws_alpha", "ws_beta", "ws_gamma"]


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def first(self):
        return None


class _Session:
    def __init__(self, users):
        self._users = users

    async def execute(self, stmt):
        params = stmt.compile().params
        uid = next((v for v in params.values() if isinstance(v, int)), None)
        return _Result(self._users.get(uid))

    async def commit(self):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2,
                data={"memberships": [{"workspace_id": w, "role": "contributor"} for w in MEMBERSHIPS]})
    app = create_app()

    async def _db():
        yield _Session({42: user})

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _answer(client, *, regime, workspaces):
    tok = delegation.mint_delegation(DLG, subject="42", regime=regime, workspaces=workspaces)
    r = client.post("/internal/validate", json={"token": tok},
                    headers={"X-Internal-Secret": INTERNAL,
                             validate_mod.ACCEPTS_DELEGATION_HEADER: validate_mod.ACCEPTS_DELEGATION_VALUE})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_ceiling_bounded_worker_is_answered_only_the_memberships_inside_it(client):
    body = _answer(client, regime="autonomous", workspaces=["ws_alpha"])
    assert body["workspaces"] == ["ws_alpha"]
    assert body["delegation"]["workspaces"] == ["ws_alpha"]


def test_narrowing_never_adds_a_workspace_the_person_is_not_a_member_of(client):
    body = _answer(client, regime="autonomous", workspaces=["ws_beta", "ws_not_a_member"])
    assert body["workspaces"] == ["ws_beta"]


def test_a_ceiling_with_none_of_the_memberships_reads_through_none(client):
    body = _answer(client, regime="autonomous", workspaces=["ws_elsewhere"])
    assert body["workspaces"] == []


def test_a_person_in_the_loop_keeps_every_membership(client):
    body = _answer(client, regime="human", workspaces="*")
    assert body["workspaces"] == MEMBERSHIPS


def test_an_api_key_is_not_narrowed():
    """The narrowing is the delegation branch's alone: `identity_of` answers an API key with every
    membership, as before."""
    user = User(id=7, email="b@example.com", max_concurrent_bots=1,
                data={"memberships": [{"workspace_id": w} for w in MEMBERSHIPS]})
    assert validate_mod.identity_of(user, scopes=["bot", "tx"], is_admin=False)["workspaces"] == MEMBERSHIPS


@pytest.mark.parametrize("ceiling,workspace,subject,expected", [
    ("*", "ws_x", "42", True),
    (["ws_x"], "ws_x", "42", True),
    (["ws_x"], "ws_y", "42", False),
    (["ws_x"], "_global", "42", True),          # the company layer is read by everyone
    (["ws_x"], "42", "42", True),               # the subject's own workspace
    ([], "ws_x", "42", False),
    (None, "ws_x", "42", False),
    ("ws_x", "ws_x", "42", False),              # a bare string is not a list of ids
])
def test_the_rule_is_the_delegation_contract_s(ceiling, workspace, subject, expected):
    assert delegation.ceiling_reads(ceiling, workspace, subject=subject) is expected
