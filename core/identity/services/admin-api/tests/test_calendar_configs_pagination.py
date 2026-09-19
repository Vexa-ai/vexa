"""Internal calendar discovery pagination, without a database or Docker."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from admin_api.app.calendars import decode_configs_cursor, encode_configs_cursor
from admin_api.app.db import get_db
from admin_api.app.main import create_app


@pytest.mark.parametrize("value", [1, 200, 2147483647])
def test_cursor_roundtrip(value):
    assert decode_configs_cursor(encode_configs_cursor(value)) == value


@pytest.mark.parametrize("cursor", ["", "!", "djE6MA", "djE6LTE", "djI6MQ", "djE6MDE", "djE6MjE0NzQ4MzY0OA"])
def test_invalid_cursor(cursor):
    with pytest.raises(HTTPException) as exc:
        decode_configs_cursor(cursor)
    assert exc.value.status_code == 422


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-secret")
    monkeypatch.setenv("DEV_MODE", "false")
    app = create_app()
    queries = []

    class DB:
        async def execute(self, query):
            queries.append(query)
            params = query.compile().params
            after = params["id_1"]
            count = query._limit_clause.value
            rows = [SimpleNamespace(id=i, data={"calendar_ics_url": f"https://calendar.test/{i}"})
                    for i in range(1, 4) if i > after][:count]
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    async def db():
        yield DB()

    app.dependency_overrides[get_db] = db
    with TestClient(app) as c:
        yield c, queries


def test_pages_and_internal_gate(client):
    c, queries = client
    assert c.get("/internal/calendar-configs").status_code == 403
    headers = {"X-Internal-Secret": "test-secret"}
    first = c.get("/internal/calendar-configs?limit=2", headers=headers).json()
    assert [v["user_id"] for v in first["configs"]] == [1, 2]
    second = c.get("/internal/calendar-configs", params={"limit": 2, "cursor": first["next_cursor"]}, headers=headers).json()
    assert [v["user_id"] for v in second["configs"]] == [3]
    assert second["next_cursor"] is None
    assert "ORDER BY users.id" in str(queries[0])
    default = c.get("/internal/calendar-configs", headers=headers)
    assert default.status_code == 200
    assert queries[-1]._limit_clause.value == 201


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"limit": "bad"}, {"cursor": "bad"}])
def test_parameter_validation(client, params):
    c, queries = client
    response = c.get("/internal/calendar-configs", params=params, headers={"X-Internal-Secret": "test-secret"})
    assert response.status_code == 422
    assert queries == []
