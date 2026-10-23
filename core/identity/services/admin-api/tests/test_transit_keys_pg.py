"""ADR-0051 / MIGRATION-0016: transit keys, minted and answered for by identity alone.

  * a target is registered (never named in code) and gets its own credential, answered once;
  * identity mints a transit key on a target — for an operator or for the target itself — hashed,
    a person's key, listed to them with its target and actions;
  * the target asks identity and gets an answer HMAC-signed under its credential; another target,
    another action, an expired key or a gateway route all refuse;
  * keys minted before transit keys existed (`vxa_tx_…`, unprefixed legacy keys) read as transit keys
    on `transcription` and keep working; a `vxa_bot_…` key does not (the rule transcription-gateway
    always applied).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.app.transit import TARGET_HEADER, TARGET_SECRET_HEADER, verify_answer
from admin_api.key_store import hash_token
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

LEGACY_TX = "vxa_tx_minted_by_transcription_gateway_0001"
LEGACY_BARE = "pre-prefix-legacy-key-abcdef0123456789"
BOT_KEY = "vxa_bot_a_gateway_key_not_for_transcription"


def _sql(url, statement, **params):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return result.all() if result.returns_rows else None
    finally:
        engine.dispose()


def _converge(url):
    engine = create_engine(url)
    try:
        ensure_schema_sync(engine, Base)
    finally:
        engine.dispose()


@pytest.fixture()
def c(pg_url, pg_async_url, monkeypatch):
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    _converge(pg_url)
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("VEXA_TERMINAL_SESSION_SECRET", "transit-test-terminal-session-secret-0123456789")
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        client.pg_url = pg_url
        client.uid = client.post("/admin/users", headers=_admin(), json={"email": "scott@example.test"}).json()["id"]
        # keys from before transit keys: written in plaintext the way transcription-gateway did, then hashed
        for key in (LEGACY_TX, LEGACY_BARE, BOT_KEY):
            _sql(pg_url, "INSERT INTO api_tokens (token, user_id, scopes, kind) VALUES (:t, :u, '{}', 'key')",
                 t=key, u=client.uid)
        _converge(pg_url)
        yield client
    _dispose_async_engine()


def _secret(c, name="transcription"):
    r = c.post(f"/admin/transit-targets/{name}/secret", headers=_admin())
    assert r.status_code == 200, r.text
    return r.json()["secret"]


def _target_headers(name, secret):
    return {TARGET_HEADER: name, TARGET_SECRET_HEADER: secret}


def _validate(c, name, secret, token, action="audio.transcribe"):
    return c.post("/transit/validate", headers=_target_headers(name, secret), json={"token": token, "action": action})


def test_transcription_is_registered_without_a_credential(c):
    targets = c.get("/admin/transit-targets", headers=_admin()).json()
    assert [(t["name"], t["actions"], t["legacy_prefixes"], t["secret_set"]) for t in targets] == [
        ("transcription", ["audio.transcribe"], ["tx", "user", "admin", ""], False)]
    assert _validate(c, "transcription", "anything", LEGACY_TX).status_code == 401


def test_existing_transcription_keys_keep_working_as_transit_keys(c):
    secret = _secret(c)
    for key in (LEGACY_TX, LEGACY_BARE):
        r = _validate(c, "transcription", secret, key)
        assert r.status_code == 200, (key, r.text)
        claims = verify_answer(secret, r.json(), audience="transcription", action="audio.transcribe")
        assert claims and claims["sub"] == c.uid and claims["email"] == "scott@example.test"
    assert _validate(c, "transcription", secret, BOT_KEY).status_code == 401


def test_a_minted_transit_key_is_hashed_listed_and_answers_only_its_target(c):
    secret = _secret(c)
    r = c.post(f"/admin/users/{c.uid}/transit-keys", headers=_admin(),
               json={"audience": "transcription", "name": "my-transcriber"})
    assert r.status_code == 201, r.text
    key = r.json()["token"]
    assert key.startswith("vxt_transcription_")
    assert _sql(c.pg_url, "SELECT token, audience, actions, kind FROM api_tokens WHERE token_hash = :h",
                h=hash_token(key)) == [(None, "transcription", ["audio.transcribe"], "key")]
    listed = c.get(f"/admin/users/{c.uid}/tokens", headers=_admin()).json()
    mine = [k for k in listed if k["name"] == "my-transcriber"][0]
    assert mine["audience"] == "transcription" and mine["token_hint"] == f"vxt_transcription_…{key[-4:]}"

    ok = _validate(c, "transcription", secret, key)
    assert verify_answer(secret, ok.json(), audience="transcription", action="audio.transcribe")
    assert verify_answer("another-secret", ok.json(), audience="transcription", action="audio.transcribe") is None
    assert _validate(c, "transcription", secret, key, action="audio.translate").status_code == 403
    # it reaches no gateway route
    assert c.post("/internal/validate", json={"token": key},
                  headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 401
    assert c.get("/user/webhook", headers={"X-API-Key": key}).status_code == 403


def test_a_second_registered_target_is_separate(c):
    tx_secret = _secret(c)
    r = c.put("/admin/transit-targets/summaries", headers=_admin(),
              json={"actions": ["summary.read"], "description": "a later pass-through service"})
    assert r.status_code == 200, r.text
    sum_secret = _secret(c, "summaries")
    tx_key = c.post(f"/admin/users/{c.uid}/transit-keys", headers=_admin(),
                    json={"audience": "transcription"}).json()["token"]
    assert _validate(c, "summaries", sum_secret, tx_key, action="summary.read").status_code == 401
    assert _validate(c, "summaries", sum_secret, LEGACY_TX, action="summary.read").status_code == 401
    assert _validate(c, "summaries", tx_secret, tx_key, action="summary.read").status_code == 401   # wrong credential
    assert c.post(f"/admin/users/{c.uid}/transit-keys", headers=_admin(),
                  json={"audience": "summaries", "actions": ["audio.transcribe"]}).status_code == 422


def test_the_target_mints_lists_and_revokes_through_identity(c):
    secret = _secret(c)
    h = _target_headers("transcription", secret)
    minted = c.post("/transit/keys", headers=h, json={"user_id": c.uid, "name": "api"})
    assert minted.status_code == 201, minted.text
    listed = c.get("/transit/keys", headers=h, params={"user_id": c.uid}).json()
    assert sorted(k["token_hint"] for k in listed) == sorted(
        [f"vxt_transcription_…{minted.json()['token'][-4:]}", "vxa_tx_…0001", "…6789"])
    assert all(not k["token_hint"].startswith("vxa_bot_") for k in listed)       # not the target's
    gone = c.delete("/transit/keys", headers=h, params={"user_id": c.uid}).json()["revoked"]
    assert gone == len(listed)
    assert _validate(c, "transcription", secret, LEGACY_TX).status_code == 401
    assert c.post("/internal/validate", json={"token": BOT_KEY},
                  headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 200   # the gateway key stays
    assert c.post("/transit/keys", headers=_target_headers("transcription", "wrong"),
                  json={"user_id": c.uid}).status_code == 401


def test_an_issued_credential_retires_the_previous_one(c):
    old = _secret(c)
    new = _secret(c)
    assert _validate(c, "transcription", old, LEGACY_TX).status_code == 401
    assert _validate(c, "transcription", new, LEGACY_TX).status_code == 200
