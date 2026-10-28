"""D13 (0.13.4): the `session` credential class — a signed-in browser is not an API key.

`POST /internal/sessions` is the one door that mints `api_tokens.kind = 'session'`: internal tier
AND `X-Terminal-Session-Secret` (= `VEXA_TERMINAL_SESSION_SECRET`). `/internal/validate` answers
`credential_class` for every bearer: `session`, `key` (legacy included) or `delegation`. The admin
mint door never produces a session. No database: a fake session that knows users and token rows.
"""
from datetime import datetime, timedelta

import pytest

from conftest import HashedTokens
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import sessions as sessions_mod
from admin_api.app import validate as validate_mod
from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.schema.models import APIToken, User

INTERNAL = "internal-secret-for-tests"
SESSION_SECRET = "terminal-session-secret-for-tests"
ADMIN = "admin-token-for-tests-0123456789"
DLG = "delegation-secret-for-tests"


class _Result:
    def __init__(self, row=None, user=None):
        self._row, self._user = row, user

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._user


class _Session:
    def __init__(self, users):
        self.users = users
        self.tokens: dict[str, APIToken] = HashedTokens()
        self.added: list = []

    async def get(self, model, ident):
        return self.users.get(ident) if model is User else None

    async def execute(self, stmt):
        params = list(stmt.compile().params.values())
        key = next((v for v in params if isinstance(v, str)), None)
        if key is not None:
            tok = self.tokens.get(key)
            return _Result(row=(tok, self.users[tok.user_id]) if tok else None)
        uid = next((v for v in params if isinstance(v, int)), None)
        return _Result(user=self.users.get(uid))

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, APIToken):
            obj.id = len(self.added)
            self.tokens[obj.token_hash] = obj

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("VEXA_TERMINAL_SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    users = {
        42: User(id=42, email="ada@example.com", max_concurrent_bots=2, data={}),
        1042: User(id=1042, email="ws-1042@principal.invalid", max_concurrent_bots=0,
                   data={"principal_kind": "workspace", "workspace_id": "ws_1"}),
    }
    session = _Session(users)
    app = create_app()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app), session


def _mint(client, body=None, *, internal=INTERNAL, terminal=SESSION_SECRET):
    headers = {}
    if internal is not None:
        headers["X-Internal-Secret"] = internal
    if terminal is not None:
        headers["X-Terminal-Session-Secret"] = terminal
    return client.post("/internal/sessions", json=body or {"user_id": 42}, headers=headers)


def _validate(client, token, **extra):
    return client.post("/internal/validate", json={"token": token},
                       headers={"X-Internal-Secret": INTERNAL, **extra})


# --- the mint door's two secrets -----------------------------------------------------------------

def test_a_session_is_minted_with_both_secrets(world):
    client, session = world
    r = _mint(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["credential_class"] == "session" and isinstance(body["id"], int)
    assert body["token"].startswith("vxa_browser_")
    row = session.tokens[body["token"]]
    assert row.kind == "session" and list(row.scopes) == ["bot", "tx", "browser"]
    assert row.name == "terminal-login" and row.user_id == 42


def test_the_session_name_and_expiry_are_honoured(world):
    client, session = world
    body = _mint(client, {"user_id": 42, "name": "my-laptop", "expires_in": 3600}).json()
    row = session.tokens[body["token"]]
    assert row.name == "my-laptop" and row.expires_at is not None


def test_an_unset_session_secret_closes_the_door(world, monkeypatch):
    client, session = world
    monkeypatch.delenv("VEXA_TERMINAL_SESSION_SECRET")
    r = _mint(client)
    assert r.status_code == 503
    assert not session.tokens


@pytest.mark.parametrize("terminal", [None, "", "wrong-secret"])
def test_a_missing_or_wrong_session_secret_is_refused(world, terminal):
    client, session = world
    assert _mint(client, terminal=terminal).status_code == 403
    assert not session.tokens


@pytest.mark.parametrize("internal", [None, "wrong"])
def test_the_internal_tier_guards_the_door_too(world, internal):
    client, session = world
    assert _mint(client, internal=internal).status_code == 403
    assert not session.tokens


def test_dev_mode_does_not_open_the_door(world, monkeypatch):
    client, session = world
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET")
    assert _mint(client, internal=None).status_code == 503
    assert not session.tokens


def test_a_workspace_principal_holds_no_session(world):
    client, session = world
    r = _mint(client, {"user_id": 1042})
    assert r.status_code == 403
    assert not session.tokens


def test_an_unknown_account_is_404(world):
    client, _ = world
    assert _mint(client, {"user_id": 9999}).status_code == 404


def test_the_body_cannot_name_a_kind_or_scopes(world):
    client, session = world
    assert _mint(client, {"user_id": 42, "kind": "key"}).status_code == 422
    assert _mint(client, {"user_id": 42, "scopes": ["bot"]}).status_code == 422
    assert not session.tokens


# --- the admin door never produces a session ----------------------------------------------------

@pytest.mark.parametrize("query,body", [
    ("scopes=bot,tx,browser&name=laptop", None),
    ("scopes=browser&kind=session", None),
    ("scope=browser", {"scopes": ["bot", "tx", "browser"], "name": "laptop"}),
])
def test_the_admin_mint_door_only_ever_makes_a_key(world, query, body):
    client, session = world
    kw = {"json": body} if body is not None else {}
    r = client.post(f"/admin/users/42/tokens?{query}", headers={"X-Admin-API-Key": ADMIN}, **kw)
    assert r.status_code == 201, r.text
    row = session.tokens[r.json()["token"]]
    assert row.kind == "key"
    assert _validate(client, r.json()["token"]).json()["credential_class"] == "key"


def test_a_body_kind_on_the_admin_door_is_refused(world):
    client, session = world
    r = client.post("/admin/users/42/tokens", headers={"X-Admin-API-Key": ADMIN},
                    json={"scopes": ["browser"], "kind": "session"})
    assert r.status_code == 422
    assert not session.tokens


# --- /internal/validate answers the class for every bearer ---------------------------------------

def test_validate_answers_session_for_a_session_row(world):
    client, _ = world
    token = _mint(client).json()["token"]
    body = _validate(client, token).json()
    assert body["credential_class"] == "session"
    assert body["scopes"] == ["bot", "tx", "browser"]


def test_validate_answers_key_for_a_browser_scoped_key(world):
    client, session = world
    session.tokens["vxa_browser_selfserve"] = APIToken(
        id=7, token="vxa_browser_selfserve", user_id=42, scopes=["bot", "tx", "browser"], kind="key")
    assert _validate(client, "vxa_browser_selfserve").json()["credential_class"] == "key"


@pytest.mark.parametrize("kind", [None, "", "weird"])
def test_validate_answers_key_for_a_legacy_or_unknown_row(world, kind):
    client, session = world
    session.tokens["legacy-token"] = APIToken(id=8, token="legacy-token", user_id=42, scopes=None, kind=kind)
    body = _validate(client, "legacy-token").json()
    assert body["credential_class"] == "key" and body["scopes"] == ["legacy"]


def test_validate_answers_delegation_for_a_worker(world):
    client, _ = world
    tok = delegation.mint_delegation(DLG, subject="42", regime="human", workspaces="*")
    body = _validate(client, tok, **{validate_mod.ACCEPTS_DELEGATION_HEADER:
                                     validate_mod.ACCEPTS_DELEGATION_VALUE}).json()
    assert body["credential_class"] == "delegation"


def test_credential_class_of_is_positive_evidence():
    assert sessions_mod.credential_class_of(APIToken(kind="session")) == "session"
    assert sessions_mod.credential_class_of(APIToken(kind="key")) == "key"
    assert sessions_mod.credential_class_of(APIToken()) == "key"


# --- no door trades a credential up to a session (B1) ------------------------------------------

def _seed(session, token, **kw):
    row = APIToken(token=token, user_id=kw.pop("user_id", 42), scopes=kw.pop("scopes", ["bot", "tx", "browser"]),
                   name=kw.pop("name", "terminal-login"), kind=kw.pop("kind", "key"),
                   created_at=kw.pop("created_at", datetime(2026, 10, 1)), **kw)
    row.id = 900 + len(session.tokens)
    session.tokens[token] = row
    return row


def _upgrade(client, token, *, internal=INTERNAL, terminal=SESSION_SECRET):
    headers = {}
    if internal is not None:
        headers["X-Internal-Secret"] = internal
    if terminal is not None:
        headers["X-Terminal-Session-Secret"] = terminal
    return client.post("/internal/sessions/upgrade", json={"token": token}, headers=headers)


@pytest.mark.parametrize("created_at", [datetime(2026, 9, 1), datetime(2026, 10, 18, 23, 59), None])
def test_a_legacy_login_key_is_never_traded_for_a_session(world, created_at):
    """The exact pre-session sign-in key, with both secrets, gets no session and is left as it was:
    the browser holding it signs in again."""
    client, session = world
    old = _seed(session, "vxa_bot_legacy-login-key", created_at=created_at)
    r = _upgrade(client, old.token)
    assert r.status_code in (404, 405), r.text
    assert len(session.tokens) == 1 and session.tokens[old.token] is old and old.kind == "key"
    assert not any(isinstance(o, APIToken) and o.kind == "session" for o in session.added)


# --- a session has an end ------------------------------------------------------------------------

def _days_left(row):
    return (row.expires_at - datetime.utcnow()) / timedelta(days=1)


def test_a_minted_session_ends_with_its_cookie(world):
    client, session = world
    row = session.tokens[_mint(client).json()["token"]]
    assert row.expires_at is not None and 29.9 < _days_left(row) <= 30


def test_a_session_lives_as_long_as_the_convergence_bounds_one():
    from admin_api.schema import sync
    assert sessions_mod.SESSION_LIFETIME_SECONDS == sync.LOGIN_LIFETIME_SECONDS == 30 * 24 * 3600


def test_a_session_cannot_be_asked_to_outlive_its_cookie(world):
    client, session = world
    row = session.tokens[_mint(client, {"user_id": 42, "expires_in": 10 ** 9}).json()["token"]]
    assert _days_left(row) <= 30


@pytest.mark.parametrize("query,body", [
    ("?name=terminal-login&scopes=bot,tx,browser", {}),
    ("?scopes=bot,tx,browser", {"name": "Terminal-Login"}),
])
def test_the_admin_door_mints_no_key_under_the_sign_in_name(world, query, body):
    client, session = world
    r = client.post(f"/admin/users/42/tokens{query}", json=body, headers={"X-Admin-API-Key": ADMIN})
    assert r.status_code == 422 and not session.tokens


def test_the_sign_in_name_is_free_where_sessions_are_off(world, monkeypatch):
    client, session = world
    monkeypatch.delenv("VEXA_TERMINAL_SESSION_SECRET")
    r = client.post("/admin/users/42/tokens?name=terminal-login&scopes=bot,tx,browser", json={},
                    headers={"X-Admin-API-Key": ADMIN})
    assert r.status_code == 201, r.text
