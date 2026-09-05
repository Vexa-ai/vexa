"""Person facts move to identity — `/user/settings` and the internal edge flows reads.

WHY THEY MOVE. `.settings.json` lived in a workspace in the AGENT domain, written by the control
MCP and read by flows (`flows_steps/common.py:setting`). That made two domains depend on a third for
a fact about a PERSON: with no agent domain deployed a person had no timezone and no mail
preferences, so flows could not state a time in their clock or honour "stop mailing me minutes".
Identity is the only domain everyone may depend on, and these six keys are facts about the person —
so identity owns them.

WHAT IS NOT HERE. `bot_name` is a fact about the BOT, not the person, and it already has a home on
this line (`users.data.calendar_bot_name` → `/internal/users/{id}/bot-context` → meeting-api's
auto-join). It is deliberately absent from this vocabulary; see the PR.

Same testcontainers-PG harness as the other identity suites (skips without docker).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _user(client, email="settings@vexa.ai"):
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    return uid, {"X-API-Key": tok}


# ── the internal edge flows reads ────────────────────────────────────────────────────────────
def test_the_internal_edge_is_closed_without_the_secret(client):
    uid, _h = _user(client)
    assert client.get(f"/internal/users/{uid}/settings").status_code in (401, 403)


def test_an_unknown_user_is_a_404_not_a_silent_default(client):
    """Defaults for a person who exists and defaults for a person who does not are opposite facts:
    the second one means flows is about to mail somebody who is not there."""
    assert client.get("/internal/users/999999/settings", headers=_internal()).status_code == 404
