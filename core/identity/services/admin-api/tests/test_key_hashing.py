"""MIGRATION-0013: a key is held as its SHA-256; its value crosses the wire once, at mint.

RTB House asked why keys were stored as written. They were: `api_tokens.token` held every key's
value, so a leaked database or backup was every customer's key. These pin the new shape:

  * the mint answers the value, and the row it writes holds only the hash and a hint;
  * a key validates by its value (identity hashes what it was handed);
  * a list shows the hint, never the value;
  * the convergence hashes a plaintext row in place, and the key it held keeps working.

The same convergence on a real Postgres, with the pre-0013 column shape, is in
`test_key_hashing_pg.py`.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api.app.db import get_db
from admin_api.app.main import create_app
from admin_api.key_store import hash_token, stored_fields, token_hint
from admin_api.schema.models import APIToken, User
from admin_api.schema.sync import _hash_api_tokens
from conftest import HashedTokens

ADMIN = "admin-secret"
INTERNAL = "internal-secret"


class _Result:
    def __init__(self, row=None, user=None, rows=None):
        self._row, self._user, self._rows = row, user, rows

    def first(self):
        return self._row

    def scalar_one_or_none(self):
        return self._user

    def scalars(self):
        return self

    def all(self):
        return self._rows or []


class _Session:
    """Holds rows the way identity does — by hash — and answers by the first string bound (a key's
    hash) or the first int (a user id)."""

    def __init__(self, users):
        self.users = users
        self.tokens = HashedTokens()
        self.added: list = []

    async def get(self, model, ident):
        return self.users.get(ident) if model is User else None

    async def execute(self, stmt):
        sql = str(stmt.compile())
        bound = stmt.compile().params
        if sql.startswith("DELETE"):
            return _Result()
        if "api_tokens.user_id =" in sql and "token_hash" not in sql.split("WHERE")[1]:
            rows = [t for t in dict.values(self.tokens) if t.user_id == bound["user_id_1"]]
            if "api_tokens.kind =" in sql:
                rows = [t for t in rows if t.kind == bound["kind_1"]]
            return _Result(rows=rows)
        params = list(bound.values())
        key = next((v for v in params if isinstance(v, str)), None)
        if key is not None:
            tok = self.tokens.get(key)
            return _Result(row=(tok, self.users[tok.user_id]) if tok else None)
        uid = next((v for v in params if isinstance(v, int)), None)
        return _Result(user=self.users.get(uid),
                       rows=[t for t in dict.values(self.tokens) if t.user_id == uid])

    def add(self, obj):
        self.added.append(obj)
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
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("VEXA_TERMINAL_SESSION_SECRET", raising=False)
    session = _Session({42: User(id=42, email="ada@example.com", max_concurrent_bots=2, data={})})
    app = create_app()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app), session


def _mint(client, **body):
    return client.post("/admin/users/42/tokens", headers={"X-Admin-API-Key": ADMIN},
                       json={"scopes": ["bot"], **body})


def _validate(client, token):
    return client.post("/internal/validate", json={"token": token}, headers={"X-Internal-Secret": INTERNAL})


def test_the_hash_is_sha256_hex_of_the_value():
    assert hash_token("vxa_bot_abc") == hashlib.sha256(b"vxa_bot_abc").hexdigest()
    assert len(hash_token("x")) == 64


@pytest.mark.parametrize("value,hint", [
    ("vxa_bot_Ab3dEf9hIjKlMnOpQrStUvWxYz012345", "vxa_bot_…2345"),
    ("vxa_browser_secretsecretWXYZ", "vxa_browser_…WXYZ"),
    ("legacy-unprefixed-token-9f8e", "…9f8e"),
])
def test_the_hint_is_the_prefix_and_the_last_four(value, hint):
    assert token_hint(value) == hint
    assert value[:-4] not in token_hint(value) or value.startswith("vxa_")


def test_a_minted_key_is_answered_once_and_stored_only_as_its_hash(world):
    client, session = world
    r = _mint(client, name="my-script")
    assert r.status_code == 201, r.text
    value = r.json()["token"]
    assert value.startswith("vxa_bot_")
    row = session.tokens[value]
    assert row.token is None
    assert row.token_hash == hashlib.sha256(value.encode()).hexdigest()
    assert row.token_hint == f"vxa_bot_…{value[-4:]}"
    # no column of the row carries the value
    assert all(value not in str(getattr(row, c.name)) for c in APIToken.__table__.columns)


def test_a_hashed_key_validates_by_its_value(world):
    client, _ = world
    value = _mint(client).json()["token"]
    r = _validate(client, value)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == 42
    assert _validate(client, value + "x").status_code == 401
    assert _validate(client, hash_token(value)).status_code == 401   # the hash is not a key


def test_a_list_shows_the_hint_and_never_the_value(world):
    client, _ = world
    value = _mint(client, name="my-script").json()["token"]
    listed = client.get("/admin/users/42/tokens", headers={"X-Admin-API-Key": ADMIN}).json()
    assert [k["token_hint"] for k in listed] == [f"vxa_bot_…{value[-4:]}"]
    assert value not in str(listed) and hash_token(value) not in str(listed)
    assert "token" not in listed[0] and "token_hash" not in listed[0]


def test_stored_fields_never_carry_the_value():
    f = stored_fields("vxa_tx_0123456789abcdefWXYZ")
    assert f["token"] is None and f["token_hash"] == hash_token("vxa_tx_0123456789abcdefWXYZ")


# --- the convergence, on the dialect-neutral path ----------------------------------------------

def _legacy_table(engine):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE api_tokens (id INTEGER PRIMARY KEY, token VARCHAR(255), "
                          "token_hash VARCHAR(64), token_hint VARCHAR(32), user_id INTEGER)"))
        conn.execute(text("INSERT INTO api_tokens (id, token, user_id) VALUES "
                          "(1, 'vxa_tx_plaintextLegacyKey0001', 7), (2, 'old-unprefixed-key-zz99', 7)"))


def test_the_convergence_hashes_plaintext_rows_in_place_and_is_idempotent():
    engine = create_engine("sqlite://")
    _legacy_table(engine)
    with engine.begin() as conn:
        _hash_api_tokens(conn)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT id, token, token_hash, token_hint FROM api_tokens ORDER BY id")).all()
    assert rows == [
        (1, None, hash_token("vxa_tx_plaintextLegacyKey0001"), "vxa_tx_…0001"),
        (2, None, hash_token("old-unprefixed-key-zz99"), "…zz99"),
    ]
    with engine.begin() as conn:            # a second boot finds nothing to do
        _hash_api_tokens(conn)
        again = conn.execute(text("SELECT id, token, token_hash, token_hint FROM api_tokens ORDER BY id")).all()
    assert again == rows


def test_a_plaintext_row_written_since_the_last_boot_still_validates(world):
    """A writer that has not moved to hashes yet (transcription-gateway during its rollout) still
    finds its key honoured; the next convergence hashes the row."""
    client, session = world
    row = APIToken(id=99, token="vxa_tx_writtenInPlaintext1234", user_id=42, scopes=["tx"], kind="key")
    session.tokens["vxa_tx_writtenInPlaintext1234"] = row
    # the fake answers by the first string bound — the hash — so this proves the lookup asks by hash
    # first and by value as the fallback (`key_store.token_matches`)
    assert _validate(client, "vxa_tx_writtenInPlaintext1234").status_code == 200
    # and the lookup that found it hashed it in place (`key_store.hash_in_place`)
    assert row.token is None and row.token_hash == hash_token("vxa_tx_writtenInPlaintext1234")
    assert row.token_hint == "vxa_tx_…1234"
