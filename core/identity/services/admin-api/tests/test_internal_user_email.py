"""`GET /internal/users/{user_id}/email` — a subject's address, for the internal tier only.

meeting-api names the people a meeting is shared with on its owner's access list; a reader who
redeemed before it kept a roster carries only an id, and this door is how that roster is
backfilled. It answers the address and the id and nothing else, and refuses anyone who is not the
internal tier. No database: a session that knows one user.
"""
import pytest
from fastapi.testclient import TestClient

from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import User

from test_delegation_workspaces import _Session, INTERNAL


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("DEV_MODE", "false")
    app = create_app()
    user = User(id=33, email="reader@example.test", max_concurrent_bots=1,
                data={"webhook_secret": "never-shipped"})

    async def _db():
        yield _Session({33: user})

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_the_internal_tier_gets_the_address_and_nothing_else(client):
    r = client.get("/internal/users/33/email", headers={"X-Internal-Secret": INTERNAL})
    assert r.status_code == 200 and r.json() == {"id": 33, "email": "reader@example.test"}


def test_an_unknown_subject_is_404(client):
    assert client.get("/internal/users/999/email", headers={"X-Internal-Secret": INTERNAL}).status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Secret": "wrong"}])
def test_anyone_else_is_refused(client, headers):
    r = client.get("/internal/users/33/email", headers=headers)
    assert r.status_code in (401, 403)
    assert "reader@example.test" not in r.text
