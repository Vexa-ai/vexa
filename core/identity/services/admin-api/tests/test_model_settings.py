"""Settings → Models (identity side) — /user/models + /user/transcription self-serve, the
platform_settings internal CRUD, and the resolution edges dispatch/bot_spawn consume.

Secrets (api_key, transcription token) are masked on every user-facing read and cross in the
clear ONLY over the X-Internal-Secret edges (`/internal/users/{id}/model-config`, bot-context).
Effective config resolves FIELD-BY-FIELD user > platform; env fallback stays downstream.

Same testcontainers-PG harness as O-STACK-3 (skips without docker).
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


def _user_token(client, email="models@vexa.ai"):
    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    return uid, tok


def test_user_models_set_masked_readback_and_clear(client):
    _uid, tok = _user_token(client)
    h = {"X-API-Key": tok}

    r = client.put("/user/models", headers=h, json={
        "mode": "custom", "model": "qwen3-coder", "base_url": "https://llm.example.com/v1",
        "api_key": "sk-secret-1234abcd",
    })
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["mode"] == "custom"
    assert cfg["model"] == "qwen3-coder"
    assert cfg["api_key_set"] is True
    # masked: the secret NEVER echoes in the clear
    assert "sk-secret" not in (cfg["api_key"] or "")
    assert cfg["api_key"].endswith("abcd")

    # partial update leaves other fields; empty string clears one
    r = client.put("/user/models", headers=h, json={"model": ""})
    cfg = r.json()
    assert cfg["model"] is None
    assert cfg["mode"] == "custom"          # untouched
    assert cfg["api_key_set"] is True       # untouched


def test_user_models_validation(client):
    _uid, tok = _user_token(client, email="val@vexa.ai")
    h = {"X-API-Key": tok}
    assert client.put("/user/models", headers=h, json={"mode": "yolo"}).status_code == 422
    assert client.put("/user/models", headers=h, json={"base_url": "not-a-url"}).status_code == 422
    assert client.put("/user/transcription", headers=h, json={"url": "ftp://x"}).status_code == 422


def test_platform_settings_crud_and_gate(client):
    # internal edge only — no/wrong secret fails closed
    assert client.get("/internal/settings/models").status_code == 403
    assert client.put("/internal/settings/models",
                      headers={"X-Internal-Secret": "wrong"}, json={}).status_code == 403
    # unknown key 404s
    assert client.get("/internal/settings/nope", headers=_internal()).status_code == 404

    r = client.put("/internal/settings/models", headers=_internal(),
                   json={"model": "haiku", "mode": "subscription"})
    assert r.status_code == 200, r.text
    assert r.json()["value"] == {"model": "haiku", "mode": "subscription"}
    # partial update + clear
    r = client.put("/internal/settings/models", headers=_internal(), json={"mode": ""})
    assert r.json()["value"] == {"model": "haiku"}
    assert client.get("/internal/settings/models", headers=_internal()).json()["value"] == {"model": "haiku"}
    # same field rules as the user tier
    assert client.put("/internal/settings/models", headers=_internal(),
                      json={"mode": "yolo"}).status_code == 422


def test_model_config_resolves_user_over_platform(client):
    uid, tok = _user_token(client, email="resolve@vexa.ai")
    client.put("/internal/settings/models", headers=_internal(),
               json={"model": "global-model", "meeting_model": "global-meeting",
                     "base_url": "https://global.example.com"})
    client.put("/user/models", headers={"X-API-Key": tok},
               json={"model": "my-model", "api_key": "sk-user-key"})

    r = client.get(f"/internal/users/{uid}/model-config", headers=_internal())
    assert r.status_code == 200, r.text
    models = r.json()["models"]
    assert models["model"] == "my-model"                       # user beats platform
    assert models["meeting_model"] == "global-meeting"         # platform fills the gap
    assert models["base_url"] == "https://global.example.com"  # field-by-field, not all-or-nothing
    assert models["api_key"] == "sk-user-key"                  # the secret crosses ONLY here

    # unknown subject → 404 (dispatch treats it as env defaults)
    assert client.get("/internal/users/999999/model-config", headers=_internal()).status_code == 404


def test_bot_context_carries_effective_transcription(client):
    uid, tok = _user_token(client, email="stt@vexa.ai")
    # nothing configured → no transcription key at all (bot_spawn keeps its env)
    r = client.get(f"/internal/users/{uid}/bot-context", headers=_internal())
    assert "transcription" not in r.json()

    client.put("/internal/settings/transcription", headers=_internal(),
               json={"url": "https://stt-global.example.com", "token": "tok-global"})
    r = client.get(f"/internal/users/{uid}/bot-context", headers=_internal())
    assert r.json()["transcription"] == {"url": "https://stt-global.example.com", "token": "tok-global"}

    client.put("/user/transcription", headers={"X-API-Key": tok},
               json={"url": "https://stt-mine.example.com"})
    r = client.get(f"/internal/users/{uid}/bot-context", headers=_internal())
    # user url wins; global token still fills the gap (field-by-field)
    assert r.json()["transcription"] == {"url": "https://stt-mine.example.com", "token": "tok-global"}

    # masked user-facing read-back
    cfg = client.get("/user/transcription", headers={"X-API-Key": tok}).json()
    assert cfg["url"] == "https://stt-mine.example.com"
    assert cfg["token_set"] is False
