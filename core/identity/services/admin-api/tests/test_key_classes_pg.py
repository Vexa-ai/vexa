"""MIGRATION-0014 on Postgres: the rows that filled customer key lists are classified `system` on
boot, the expired ones are deleted, and every key a person made stays where it was.

Shapes are the ones the 2026-10-23 prod read found (#1868): `webapp-login` keys with and without an
end, flows' unnamed 15-minute keys, unnamed keys with no end (the old dashboard's), `mcp-connect`.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.key_store import hash_token
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

SESSION_SECRET = "pg-test-terminal-session-secret-0123456789abcdef"

NOW = datetime.utcnow().replace(microsecond=0)


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


def _row(url, uid, key, *, name, kind="key", created_at, expires_at=None, scopes=("bot", "tx", "browser")):
    _sql(url, "INSERT INTO api_tokens (token_hash, token_hint, user_id, scopes, name, kind, created_at, expires_at) "
              "VALUES (:h, 'x', :u, :s, :n, :k, :c, :e)",
         h=hash_token(key), u=uid, s=list(scopes), n=name, k=kind, c=created_at, e=expires_at)


def _kind(url, key):
    rows = _sql(url, "SELECT kind, expires_at FROM api_tokens WHERE token_hash = :h", h=hash_token(key))
    return rows[0] if rows else None


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    _converge(pg_url)
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("VEXA_TERMINAL_SESSION_SECRET", SESSION_SECRET)
    app_db.configure(pg_async_url)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        c.pg_url = pg_url
        c.uid = c.post("/admin/users", headers=_admin(), json={"email": "scott@example.test"}).json()["id"]
        yield c
    _dispose_async_engine()


def _validates(c, key):
    return c.post("/internal/validate", json={"token": key},
                  headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 200


def _listed(c, **q):
    return [k["name"] for k in c.get(f"/admin/users/{c.uid}/tokens", headers=_admin(), params=q).json()]


def test_the_boot_hides_and_sweeps_what_the_product_minted_and_keeps_what_the_person_made(client):
    c, u = client, client.uid
    old, recent = NOW - timedelta(days=80), NOW - timedelta(days=3)
    _row(c.pg_url, u, "vxa_bot_webappOldNoEnd", name="webapp-login", created_at=old)            # pre-Sep cookie key
    _row(c.pg_url, u, "vxa_bot_webappRecent", name="webapp-login", created_at=recent,
         expires_at=recent + timedelta(days=30))
    _row(c.pg_url, u, "vxa_bot_flows15min", name=None, created_at=old, expires_at=old + timedelta(minutes=15),
         scopes=("bot", "browser", "tx"))                                          # as flows minted it
    _row(c.pg_url, u, "vxa_bot_dashboardForever", name=None, created_at=old)                     # no end: untouched
    _row(c.pg_url, u, "vxa_bot_mcpConnect", name="mcp-connect", created_at=old)
    _row(c.pg_url, u, "vxa_bot_myExpiredKey", name="my-ci", created_at=old, expires_at=old + timedelta(days=1))
    _row(c.pg_url, u, "vxa_browser_oldSession", name="terminal-login", kind="session", created_at=old,
         expires_at=old + timedelta(days=30))
    assert len(_listed(c)) == 6                       # before: the person sees the product's keys too

    _converge(c.pg_url)                               # the upgraded admin-api's boot

    # the product's expired credentials are gone
    assert _kind(c.pg_url, "vxa_bot_webappOldNoEnd") is None
    assert _kind(c.pg_url, "vxa_bot_flows15min") is None
    assert _kind(c.pg_url, "vxa_browser_oldSession") is None
    # a live cookie key is now system: hidden, still working, its end unchanged
    assert _kind(c.pg_url, "vxa_bot_webappRecent") == ("system", recent + timedelta(days=30))
    assert _validates(c, "vxa_bot_webappRecent")
    # what the person made is untouched and listed, an expired key of theirs included
    assert sorted(_listed(c), key=str) == sorted([None, "mcp-connect", "my-ci"], key=str)
    assert _kind(c.pg_url, "vxa_bot_dashboardForever") == ("key", None) and _validates(c, "vxa_bot_dashboardForever")
    assert _validates(c, "vxa_bot_mcpConnect")
    assert "webapp-login" in _listed(c, include_hidden="true")

    before = _sql(c.pg_url, "SELECT id, kind, expires_at FROM api_tokens ORDER BY id")
    _converge(c.pg_url)                               # idempotent
    assert _sql(c.pg_url, "SELECT id, kind, expires_at FROM api_tokens ORDER BY id") == before


def test_every_mint_sweeps_that_persons_dead_hidden_credentials(client):
    c, u = client, client.uid
    _row(c.pg_url, u, "vxa_bot_deadSystem", name="sso", kind="system", created_at=NOW - timedelta(days=2),
         expires_at=NOW - timedelta(days=1))
    _row(c.pg_url, u, "vxa_bot_deadOwnKey", name="mine", created_at=NOW - timedelta(days=2),
         expires_at=NOW - timedelta(days=1))
    other = c.post("/admin/users", headers=_admin(), json={"email": "other@example.test"}).json()["id"]
    _row(c.pg_url, other, "vxa_bot_othersDeadSystem", name="sso", kind="system",
         created_at=NOW - timedelta(days=2), expires_at=NOW - timedelta(days=1))

    r = c.post(f"/admin/users/{u}/tokens", headers=_admin(), json={"scopes": ["bot"], "name": "webapp-login"})
    assert r.status_code == 201, r.text
    assert _kind(c.pg_url, "vxa_bot_deadSystem") is None
    assert _kind(c.pg_url, "vxa_bot_deadOwnKey") is not None          # a person's own key is theirs
    assert _kind(c.pg_url, "vxa_bot_othersDeadSystem") is not None    # another person's waits for a boot
    assert _kind(c.pg_url, r.json()["token"])[0] == "system"
    assert "webapp-login" not in _listed(c)


def test_sign_out_ends_a_session_and_never_a_persons_key(client):
    c = client
    minted = c.post("/internal/sessions", json={"user_id": c.uid},
                    headers={"X-Internal-Secret": INTERNAL_SECRET, "X-Terminal-Session-Secret": SESSION_SECRET})
    assert minted.status_code == 201, minted.text
    session = minted.json()["token"]
    own = c.post(f"/admin/users/{c.uid}/tokens", headers=_admin(), json={"scopes": ["bot"], "name": "mine"}).json()["token"]
    end = lambda tok, secret=INTERNAL_SECRET: c.post("/internal/sessions/end", json={"token": tok},
                                                     headers={"X-Internal-Secret": secret})
    assert end(session, "wrong").status_code == 403
    assert _validates(c, session)
    assert end(session).status_code == 204
    assert not _validates(c, session) and _kind(c.pg_url, session) is None
    assert end(own).status_code == 204                   # same answer, nothing deleted
    assert _validates(c, own)
    assert end("vxa_bot_never_existed").status_code == 204


def test_the_boot_never_reclassifies_a_persons_own_key(client):
    """MIGRATION-0014's guards: a sign-in name counts only with the `browser` scope every sign-in
    minted with, and an unnamed key only in the exact shape flows minted ([bot, browser, tx], a day
    or less). A person's keys outside those shapes stay theirs, listed and working."""
    c, u = client, client.uid
    old = NOW - timedelta(days=80)
    _row(c.pg_url, u, "vxa_bot_namedLikeSignInNoBrowser", name="webapp-login", created_at=old, scopes=("bot", "tx"))
    _row(c.pg_url, u, "vxa_bot_myShortUnnamedKey", name=None, created_at=NOW - timedelta(hours=1),
         expires_at=NOW + timedelta(hours=1), scopes=("bot", "tx"))
    _row(c.pg_url, u, "vxa_bot_flowsShaped", name=None, created_at=old, expires_at=old + timedelta(minutes=15),
         scopes=("bot", "browser", "tx"))
    _converge(c.pg_url)
    assert _kind(c.pg_url, "vxa_bot_namedLikeSignInNoBrowser") == ("key", None)
    assert _kind(c.pg_url, "vxa_bot_myShortUnnamedKey")[0] == "key"
    assert _kind(c.pg_url, "vxa_bot_flowsShaped") is None          # flows' leftover: classified, expired, gone
    assert _validates(c, "vxa_bot_namedLikeSignInNoBrowser") and _validates(c, "vxa_bot_myShortUnnamedKey")
