"""Account ramp settings: pure models plus HTTP edges over a stub session (no database)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from admin_api.app import main


@pytest.mark.parametrize("fields", [
    {"ramp_bots": 0}, {"ramp_window_s": 1}, {"ramp_bots": 4, "ramp_window_s": 90},
])
def test_ramp_only_patch_is_a_change(fields):
    patch = main.UserAdminPatch(**fields)
    assert patch.model_dump(exclude_unset=True) == fields


@pytest.mark.parametrize("fields", [
    {"ramp_bots": -1}, {"ramp_window_s": 0}, {}, {"ramp_bots": None},
])
def test_invalid_or_empty_ramp_patch_is_refused(fields):
    with pytest.raises(ValidationError):
        main.UserAdminPatch(**fields)


@pytest.mark.parametrize("data,expected", [
    ({}, (7, 91)), ({"ramp_bots": 0}, (0, 91)),
    ({"ramp_window_s": 1}, (7, 1)),
    ({"ramp_bots": 4, "ramp_window_s": 80}, (4, 80)),
])
def test_resolve_ramp_defaults_each_missing_key(data, expected):
    assert main.resolve_ramp(data, (7, 91)) == expected


@pytest.mark.parametrize("data,expected", [({}, (7, 91)), ({"ramp_bots": 0}, (0, 91))])
def test_read_model_resolves_env_defaults_without_changing_data(monkeypatch, data, expected):
    monkeypatch.setenv("VEXA_RAMP_BOTS_DEFAULT", "7")
    monkeypatch.setenv("VEXA_RAMP_WINDOW_S_DEFAULT", "91")
    user = SimpleNamespace(id=7, email="ramp@example.test", name=None,
                           max_concurrent_bots=3, data=dict(data))
    response = main.UserResponse.model_validate(user).model_dump()
    assert (response["ramp_bots"], response["ramp_window_s"]) == expected
    assert response["data"] == user.data == data


def test_patch_and_both_internal_edges_share_resolved_ramp(monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-test")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-test")
    monkeypatch.setenv("VEXA_RAMP_BOTS_DEFAULT", "7")
    monkeypatch.setenv("VEXA_RAMP_WINDOW_S_DEFAULT", "91")
    user = SimpleNamespace(id=7, email="ramp@example.test", name=None,
                           max_concurrent_bots=3, data={"calendar_bot_name": "Calendar"})
    token = SimpleNamespace(expires_at=None, scopes=["bot"], last_used_at=None)
    result = Mock()
    result.first.return_value = (token, user)
    result.scalar_one_or_none.return_value = user
    db = SimpleNamespace(execute=AsyncMock(return_value=result), get=AsyncMock(return_value=None),
                         commit=AsyncMock(), refresh=AsyncMock())
    app = main.create_app()
    app.dependency_overrides[main.get_db] = lambda: db
    client = TestClient(app)
    internal = {"X-Internal-Secret": "internal-test"}

    def read_edges(expected):
        responses = [
            client.post("/internal/validate", json={"token": "test"}, headers=internal),
            client.get("/internal/users/7/bot-context", headers=internal),
        ]
        for response in responses:
            assert response.status_code == 200, response.text
            assert (response.json()["ramp_bots"], response.json()["ramp_window_s"]) == expected

    read_edges((7, 91))
    response = client.patch("/admin/users/7", headers={"X-Admin-API-Key": "admin-test"},
                            json={"ramp_bots": 0, "ramp_window_s": 60})
    assert response.status_code == 200, response.text
    assert response.json()["ramp_bots"] == 0
    assert response.json()["ramp_window_s"] == 60
    assert user.data == {"calendar_bot_name": "Calendar", "ramp_bots": 0, "ramp_window_s": 60}
    assert user.max_concurrent_bots == 3
    read_edges((0, 60))


@pytest.mark.parametrize("data,expected", [
    ({"ramp_bots": "4", "ramp_window_s": "80"}, (4, 80)),
    ({"ramp_bots": "0", "ramp_window_s": "1"}, (0, 1)),
    ({"ramp_bots": None, "ramp_window_s": None}, (7, 91)),
    ({"ramp_bots": "invalid", "ramp_window_s": "80"}, (7, 80)),
    ({"ramp_bots": "4", "ramp_window_s": {}}, (4, 91)),
])
def test_resolve_ramp_coerces_values_or_uses_defaults(data, expected):
    assert main.resolve_ramp(data, (7, 91)) == expected
