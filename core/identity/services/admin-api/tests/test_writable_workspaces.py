"""`/internal/validate` names the workspaces the caller may WRITE into (contributor or owner).

meeting-api refuses to bind a meeting into a workspace its owner may only read, and it learns which
those are from this answer (signed by the gateway as gateway-identity.v1 `writable_workspaces`). A
viewer membership is listed in `workspaces` and absent from `writable_workspaces`; a worker's write
set is narrowed by the same ceiling as its read set. No database: a session that knows one user.
"""
import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

from test_delegation_workspaces import _Session, DLG, INTERNAL

ROLES = {"ws_owner": "owner", "ws_edit": "contributor", "ws_view": "viewer", "ws_oddcase": "Contributor"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    user = User(id=42, email="ada@example.com", max_concurrent_bots=2,
                data={"memberships": [{"workspace_id": w, "role": r} for w, r in ROLES.items()]})
    app = create_app()

    async def _db():
        yield _Session({42: user})

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def _validate(client, token):
    r = client.post("/internal/validate", json={"token": token},
                    headers={"X-Internal-Secret": INTERNAL,
                             validate_mod.ACCEPTS_DELEGATION_HEADER: validate_mod.ACCEPTS_DELEGATION_VALUE})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_viewer_membership_is_readable_but_not_writable(client):
    body = _validate(client, delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*"))
    assert set(body["workspaces"]) == set(ROLES)
    assert set(body["writable_workspaces"]) == {"ws_owner", "ws_edit", "ws_oddcase"}
    assert "ws_view" not in body["writable_workspaces"]


def test_a_workers_write_set_is_narrowed_by_its_ceiling(client):
    body = _validate(client, delegation.mint_delegation(DLG, subject="42", regime="autonomous",
                                                        workspaces=["ws_edit", "ws_view"]))
    assert body["writable_workspaces"] == ["ws_edit"]
