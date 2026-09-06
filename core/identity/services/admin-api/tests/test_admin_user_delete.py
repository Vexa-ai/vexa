"""The two admin-tier routes PRD decision 38 needs: delete ONE person, and bind ONE person's
model config.

Both existed nowhere. Removing somebody meant `blank-instance.sh`, which deletes every person on
the stack — the instrument for "reset one test subject" was a wipe of the whole instance. And
pinning a config to somebody else meant the platform setting, which changes the model for everyone.

Same testcontainers-PG harness as the rest of this suite (skips without docker).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

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
        c.pg_url = pg_url
        yield c
    _dispose_async_engine()


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _user(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def _sql(client, statement, **params):
    """Raw SQL beside the app, for the rows no route creates. Reads return rows; writes return []."""
    engine = create_engine(client.pg_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return list(result) if result.returns_rows else []
    finally:
        engine.dispose()


# ── DELETE /admin/users/{id} ─────────────────────────────────────────────────────────────────────

def test_delete_removes_the_user_and_their_tokens(client):
    uid = _user(client, "gone@rehearse.test")
    client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin())
    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 204
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 404
    assert _sql(client, "SELECT id FROM api_tokens WHERE user_id = :u", u=uid) == []


def test_delete_takes_the_meetings_sessions_and_transcripts_with_it(client):
    """`meetings.user_id` is a plain Integer with NO ForeignKey to users, so nothing cascades.
    Deleting the row alone would leave that person's meetings owned by an id that names nobody —
    the ghost-identity failure, one layer down."""
    uid = _user(client, "withdata@rehearse.test")
    _sql(client, "INSERT INTO meetings (id, user_id, platform, status) "
                 "VALUES (9001, :u, 'jitsi', 'completed')", u=uid)
    _sql(client, "INSERT INTO meeting_sessions (id, meeting_id, session_uid, session_start_time) "
                 "VALUES (9101, 9001, 'sess-1', now())")
    _sql(client, "INSERT INTO transcriptions (id, meeting_id, start_time, end_time, text) "
                 "VALUES (9201, 9001, 0, 1, 'hello')")

    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 204

    assert _sql(client, "SELECT id FROM transcriptions WHERE meeting_id = 9001") == []
    assert _sql(client, "SELECT id FROM meeting_sessions WHERE meeting_id = 9001") == []
    assert _sql(client, "SELECT id FROM meetings WHERE id = 9001") == []


def test_delete_leaves_everyone_else_exactly_where_it_found_them(client):
    keep = _user(client, "keep@rehearse.test")
    _sql(client, "INSERT INTO meetings (id, user_id, platform, status) "
                 "VALUES (9002, :u, 'jitsi', 'completed')", u=keep)
    gone = _user(client, "gone2@rehearse.test")
    client.delete(f"/admin/users/{gone}", headers=_admin())
    assert client.get(f"/admin/users/{keep}", headers=_admin()).status_code == 200
    assert len(_sql(client, "SELECT id FROM meetings WHERE id = 9002")) == 1


def test_delete_needs_the_admin_token(client):
    uid = _user(client, "guarded@rehearse.test")
    assert client.delete(f"/admin/users/{uid}").status_code in (401, 403)
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 200


def test_deleting_an_unknown_user_is_a_404_not_a_silent_success(client):
    assert client.delete("/admin/users/424242", headers=_admin()).status_code == 404


# ── PUT /admin/users/{id}/models ─────────────────────────────────────────────────────────────────

def test_an_admin_can_bind_one_subject_to_a_runner_and_an_endpoint(client):
    uid = _user(client, "qwen@rehearse.test")
    r = client.put(f"/admin/users/{uid}/models", headers=_admin(), json={
        "runner": "openai-agent", "mode": "custom",
        "base_url": "http://192.168.1.6:8001/v1", "model": "qwen3.8-27b",
        "extra_body": '{"chat_template_kwargs":{"enable_thinking":false}}'})
    assert r.status_code == 200, r.text
    eff = client.get(f"/internal/users/{uid}/model-config", headers=_internal()).json()["models"]
    assert eff["runner"] == "openai-agent"
    assert eff["model"] == "qwen3.8-27b"
    assert eff["base_url"] == "http://192.168.1.6:8001/v1"
    assert eff["extra_body"] == '{"chat_template_kwargs":{"enable_thinking":false}}'


def test_binding_one_subject_leaves_every_other_subject_on_the_deployment_default(client):
    """The whole point of the route: the founder's dispatches are untouched by CONSTRUCTION.
    A platform setting would have changed the model for everybody."""
    scratch = _user(client, "scratch@rehearse.test")
    founder = _user(client, "founder@vexa.ai")
    client.put(f"/admin/users/{scratch}/models", headers=_admin(),
               json={"runner": "openai-agent", "mode": "custom",
                     "base_url": "http://192.168.1.6:8001/v1"})
    other = client.get(f"/internal/users/{founder}/model-config",
                       headers=_internal()).json()["models"]
    assert other == {}


def test_an_empty_string_clears_a_field(client):
    """How `runner="claude-code"` returns a subject to the deployment's own harness: the custom
    endpoint is CLEARED, not left pointing at a model the subject no longer runs."""
    uid = _user(client, "clearme@rehearse.test")
    client.put(f"/admin/users/{uid}/models", headers=_admin(),
               json={"runner": "openai-agent", "mode": "custom",
                     "base_url": "http://192.168.1.6:8001/v1", "model": "qwen3.8-27b"})
    client.put(f"/admin/users/{uid}/models", headers=_admin(),
               json={"runner": "claude-code", "mode": "", "base_url": "", "model": "",
                     "extra_body": ""})
    eff = client.get(f"/internal/users/{uid}/model-config", headers=_internal()).json()["models"]
    assert eff == {"runner": "claude-code"}


def test_the_admin_route_masks_the_secret_like_the_self_serve_one(client):
    uid = _user(client, "secret@rehearse.test")
    body = client.put(f"/admin/users/{uid}/models", headers=_admin(),
                      json={"mode": "custom", "base_url": "https://gw.test/v1",
                            "api_key": "sk-super-secret-value"}).json()
    assert body["api_key_set"] is True
    assert "sk-super-secret-value" not in str(body)


def test_it_needs_the_admin_token_and_404s_on_an_unknown_user(client):
    uid = _user(client, "guard2@rehearse.test")
    assert client.put(f"/admin/users/{uid}/models", json={"runner": "openai-agent"}).status_code \
        in (401, 403)
    assert client.put("/admin/users/424242/models", headers=_admin(),
                      json={"runner": "openai-agent"}).status_code == 404
