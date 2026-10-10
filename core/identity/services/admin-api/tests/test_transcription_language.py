"""A person's default transcription language (transcription-language.v1 user tier), identity's half.

The rulebook reads the contract's goldens the way the contract's validate.mjs and meeting-api do; the
doors store it on the person, hand it back masked of nothing (it is not a secret), and give it to
meetings in the bot-context answer only when the person set one. The door tests use the
testcontainers-PG harness (skip without docker).
"""
import json
from pathlib import Path

import pytest

from admin_api.app import transcription_language as tl


def _golden_dir() -> Path:
    rel = Path("core") / "meetings" / "contracts" / "transcription-language.v1" / "golden"
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_dir():
            return parent / rel
    raise FileNotFoundError(str(rel))


def _user_goldens():
    for path in sorted(_golden_dir().glob("*.json")):
        doc = json.loads(path.read_text())
        shape = path.name.split(".")[0]
        if shape in ("UserPreferenceUpdate", "UserPreference"):
            yield pytest.param(doc, True, id=path.name)
        elif shape == "Refused" and doc["shape"] in ("UserPreferenceUpdate", "SpawnFields", "ConfigUpdate") \
                and set(doc["value"]) <= {"language", "allowed_languages"} \
                and doc["value"].get("language") != "auto" and doc["value"]:
            yield pytest.param(doc["value"], False, id=path.name)


@pytest.mark.parametrize("update,accepted", list(_user_goldens()))
def test_the_rulebook_reads_the_contracts_goldens(update, accepted):
    if accepted:
        tl.apply({}, update)
    else:
        with pytest.raises(tl.Refused):
            tl.apply({}, update)


def test_a_change_to_one_half_is_checked_against_the_stored_other_half():
    stored = tl.apply({}, {"language": "de", "allowed_languages": ["de", "en"]})
    with pytest.raises(tl.Refused):
        tl.apply(stored, {"language": "fr"})
    with pytest.raises(tl.Refused):
        tl.apply(stored, {"allowed_languages": ["fr", "en"]})
    assert tl.apply(stored, {"language": ""}) == {"allowed_languages": ["de", "en"]}


def test_clearing_both_leaves_nothing_for_bot_context():
    stored = tl.apply({"url": "https://stt.example.com"}, {"language": "de"})
    cleared = tl.apply(stored, {"language": "", "allowed_languages": []})
    assert cleared == {"url": "https://stt.example.com"}
    assert tl.for_bot_context(cleared) is None
    assert tl.for_bot_context(stored) == {"language": "de", "allowed_languages": []}


# ── the doors (testcontainers PG) ──────────────────────────────────────────────────────────────────

from conftest import requires_docker  # noqa: E402


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    from test_model_settings import client as _client_fixture  # noqa: F401 — same harness
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from admin_api.app import db as app_db
    from admin_api.app.main import create_app
    from admin_api.schema.models import Base
    from admin_api.schema.sync import ensure_schema_sync
    from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _dispose_async_engine

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


def _user(client, email):
    from test_stack_admin_api import _admin

    uid = client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]
    return uid, {"X-API-Key": tok}


def _context(client, uid):
    from test_stack_admin_api import INTERNAL_SECRET

    return client.get(f"/internal/users/{uid}/bot-context",
                      headers={"X-Internal-Secret": INTERNAL_SECRET}).json()


@requires_docker
def test_the_persons_default_is_stored_read_back_and_given_to_meetings(client):
    uid, h = _user(client, "lang@vexa.ai")
    assert "transcription_language" not in _context(client, uid)
    assert client.get("/user/transcription", headers=h).json()["language"] is None

    r = client.put("/user/transcription", headers=h,
                   json={"language": "de", "allowed_languages": ["de", "en"]})
    assert r.status_code == 200, r.text
    assert r.json()["language"] == "de" and r.json()["allowed_languages"] == ["de", "en"]
    assert _context(client, uid)["transcription_language"] == {
        "language": "de", "allowed_languages": ["de", "en"]}
    # the backend half of the same door is untouched by a language-only change
    assert "transcription" not in _context(client, uid)

    client.put("/user/transcription", headers=h, json={"language": "", "allowed_languages": []})
    assert "transcription_language" not in _context(client, uid)


@requires_docker
@pytest.mark.parametrize("body", [
    {"language": "German"}, {"allowed_languages": "de,en"},
    {"language": "fr", "allowed_languages": ["de", "en"]}, {"allowed_languages": ["de", "de"]},
])
def test_a_refused_default_is_a_422_and_nothing_is_stored(client, body):
    uid, h = _user(client, "lang-bad@vexa.ai")
    r = client.put("/user/transcription", headers=h, json=body)
    assert r.status_code == 422, r.text
    assert "transcription_language" not in _context(client, uid)
