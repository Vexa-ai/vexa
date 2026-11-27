"""The connect route (app/connect.py, signin.v1 ConnectRequest/ConnectResponse).

A verified sign-in elsewhere (the brain's Google sign-in, through the credential broker) gets its
Vexa user and that user's own key, once. The raw ID token is the only credential: no admin key, no
internal secret, no email parameter.

A real Postgres (testcontainers), so two connects at once meet the `users.email` UNIQUE index and the
user row's lock exactly as they would in production. The token is signed with a generated RSA key and
the issuer's keys are a fake fetch (`idp_tokens._fetch_json`, the seam the photo tests replace); the
service Redis is an in-memory counter (`signin_links._client`).
"""
from __future__ import annotations

import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from admin_api import obs
from admin_api.app import admin_account
from admin_api.app import connect as connect_mod
from admin_api.app import db as app_db
from admin_api.app import idp_tokens
from admin_api.app import signin_links
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker

BRAIN_CLIENT = "123456789012-brainsignin0001.apps.googleusercontent.com"
TERMINAL_CLIENT = "123456789012-terminalsignin01.apps.googleusercontent.com"
OTHER_CLIENT = "210987654321-somebodyelse0001.apps.googleusercontent.com"
EMAIL = "ana@corp.example"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key, kid):
    j = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**j, "kid": kid, "alg": "RS256", "use": "sig"}


JWKS = {"keys": [_jwk(KEY, "k1")]}


def id_token(*, key=KEY, kid="k1", **over):
    """A fresh Google ID token; each call is a different token (its own jti), as Google's are."""
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": BRAIN_CLIENT, "azp": BRAIN_CLIENT, "sub": "1234567890",
              "email": EMAIL, "email_verified": True, "name": "Ana", "iat": now, "exp": now + 600,
              "jti": secrets.token_hex(8), **over}
    claims = {k: v for k, v in claims.items() if v is not None or k == "email_verified"}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class FakeCounter:
    """The service Redis as the connect route sees it: INCR + EXPIRE (the cap) and SET NX EX (a spent
    token). `down` fails every call."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.spent: dict[str, int] = {}
        self.down = False

    async def set(self, key, value, *, nx=False, ex=None):
        if self.down:
            raise ConnectionError("redis down")
        if nx and key in self.spent:
            return None
        self.spent[key] = ex
        return True

    async def incr(self, key):
        if self.down:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        return True


@pytest.fixture()
def counter(monkeypatch):
    c = FakeCounter()
    monkeypatch.setattr(signin_links, "_client", c)
    return c


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch, counter):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", TERMINAL_CLIENT)
    monkeypatch.setenv("VEXA_CONNECT_GOOGLE_CLIENT_IDS", f"555555555555-anotherlisted001.apps.googleusercontent.com, {BRAIN_CLIENT}")
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@corp.example")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("VEXA_ADMIN_ACCOUNT", raising=False)

    async def fetch(url):
        return JWKS

    idp_tokens.reset()
    monkeypatch.setattr(idp_tokens, "_fetch_json", fetch)
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()
    idp_tokens.reset()


def _rows(pg_url, sql, **params):
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).fetchall()
    finally:
        engine.dispose()


def _users(pg_url, email=EMAIL):
    return _rows(pg_url, "SELECT id FROM users WHERE lower(email) = :e", e=email.lower())


def _keys(pg_url, user_id):
    return _rows(pg_url, "SELECT token, scopes, kind, expires_at FROM api_tokens WHERE user_id = :u AND name = :n",
                 u=user_id, n=connect_mod.KEY_NAME)


def _validate(client, key):
    return client.post("/internal/validate", headers={"X-Internal-Secret": INTERNAL_SECRET}, json={"token": key})


def connect(client, token=None, **body):
    return client.post("/connect", json={"provider": "google", "id_token": token or id_token(), **body})


# ── the card's check, one test per line ───────────────────────────────────────────────────────

def test_two_connects_at_once_for_one_email_give_one_user(client, pg_url):
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(lambda _: connect(client), range(4)))
    assert [a.status_code for a in answers] == [200] * 4, [a.text for a in answers]
    assert len({a.json()["user_id"] for a in answers}) == 1
    assert len(_users(pg_url)) == 1
    assert len(_keys(pg_url, answers[0].json()["user_id"])) == 1, "two connects at once leave one live key"


def test_an_email_that_already_has_a_user_gets_that_user(client, pg_url):
    existing = client.post("/admin/users", headers=_admin(), json={"email": "Ana@Corp.example"}).json()["id"]
    r = connect(client)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == existing and r.json()["bound"] == "first"
    assert len(_users(pg_url)) == 1


def test_the_key_is_the_users_own_and_is_answered_once(client, pg_url):
    r = connect(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"user_id", "key", "key_expires_at", "bound"}
    v = _validate(client, body["key"])
    assert v.status_code == 200, v.text
    assert v.json()["user_id"] == body["user_id"]
    assert body["key"] != ADMIN_TOKEN
    token, scopes, kind, expires_at = _keys(pg_url, body["user_id"])[0]
    assert token == body["key"] and kind == "key" and sorted(scopes) == sorted(connect_mod.KEY_SCOPES)
    days = (expires_at.timestamp() - time.time()) / 86400
    assert 364 < days <= 365
    # Answered once: no read of the user's keys carries the value.
    listed = client.get(f"/admin/users/{body['user_id']}/tokens", headers=_admin())
    assert listed.status_code == 200 and body["key"] not in listed.text


def test_a_reconnect_ends_the_earlier_key_so_one_is_live(client, pg_url):
    first = connect(client).json()
    second = connect(client).json()
    assert second["user_id"] == first["user_id"] and second["bound"] == "same"
    assert second["key"] != first["key"]
    assert [row[0] for row in _keys(pg_url, first["user_id"])] == [second["key"]]
    assert _validate(client, first["key"]).status_code == 401
    assert _validate(client, second["key"]).status_code == 200


def test_an_unverified_email_is_refused_and_nothing_is_created(client, pg_url):
    for claims in ({"email_verified": False}, {"email_verified": "false"}, {"email_verified": None}):
        r = connect(client, id_token(**claims))
        assert r.status_code == 401, r.text
    assert _users(pg_url) == []
    assert _rows(pg_url, "SELECT id FROM api_tokens") == []


def test_a_token_for_another_audience_is_refused(client, pg_url):
    for aud in (OTHER_CLIENT, TERMINAL_CLIENT):
        r = connect(client, id_token(aud=aud))
        assert r.status_code == 401, (aud, r.text)
    assert _users(pg_url) == []


def test_a_token_not_signed_by_the_issuer_or_expired_is_refused(client, pg_url):
    assert connect(client, id_token(key=OTHER_KEY)).status_code == 401
    assert connect(client, id_token(exp=int(time.time()) - 3600)).status_code == 401
    assert connect(client, id_token(iss="https://evil.example")).status_code == 401
    assert _users(pg_url) == []


def test_with_the_setting_unset_any_token_is_refused(client, pg_url, monkeypatch):
    monkeypatch.delenv("VEXA_CONNECT_GOOGLE_CLIENT_IDS")
    r = connect(client)
    assert r.status_code == 401, r.text
    # the photo check's audience is not the connect route's
    assert connect(client, id_token(aud=TERMINAL_CLIENT)).status_code == 401
    assert _users(pg_url) == []


def test_a_refused_admission_creates_no_user(client, pg_url):
    r = connect(client, id_token(email="stranger@elsewhere.example"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "not-allowed"
    assert _users(pg_url, "stranger@elsewhere.example") == []


def test_an_account_bound_to_another_google_identity_is_refused(client, pg_url):
    uid = connect(client).json()["user_id"]
    before = _keys(pg_url, uid)
    r = connect(client, id_token(sub="999"))
    assert r.status_code == 409, r.text
    assert _keys(pg_url, uid) == before, "nothing changes"


def test_connects_for_one_subject_are_capped(client, counter):
    for _ in range(connect_mod.RATE_LIMIT):
        assert connect(client).status_code == 200
    assert connect(client).status_code == 429
    assert connect(client, id_token(sub="another", email="bo@corp.example")).status_code == 200


def test_a_rate_store_that_cannot_answer_refuses(client, counter, pg_url):
    counter.down = True
    assert connect(client).status_code == 503
    assert _users(pg_url) == []


def test_the_body_is_held_to_its_shape(client):
    assert client.post("/connect", json={"provider": "google", "id_token": id_token(), "email": EMAIL}).status_code == 422
    assert client.post("/connect", json={"provider": "microsoft", "id_token": id_token()}).status_code == 422
    assert client.post("/connect", json={"provider": "google", "id_token": "x" * 16385}).status_code == 422
    assert client.post("/connect", json={}).status_code == 422


def test_the_admin_key_is_in_no_response_and_no_log_line(client):
    sink: list = []
    obs.capture(sink)
    try:
        answers = [connect(client), connect(client), connect(client, id_token(aud=OTHER_CLIENT)),
                   connect(client, id_token(email="stranger@elsewhere.example"))]
    finally:
        obs.capture(None)
    keys = [a.json()["key"] for a in answers if a.status_code == 200]
    lines = json.dumps(sink)
    for a in answers:
        assert ADMIN_TOKEN not in a.text and INTERNAL_SECRET not in a.text
    assert ADMIN_TOKEN not in lines and INTERNAL_SECRET not in lines
    for k in keys:
        assert k not in lines
    audits = [e for e in sink if e.get("event") == "connect_issued"]
    assert [(e["fields"]["provider"], e["fields"]["bound"]) for e in audits] == [("google", "first"), ("google", "same")]
    assert all("." not in json.dumps(e["fields"]) for e in audits), "no token in the audit line"


# ── fix round (reviews 178030, 177958) ──────────────────────────────────────────────────────────

def test_a_token_works_once_and_a_replay_ends_nothing(client, pg_url, counter):
    token = id_token()
    first = connect(client, token)
    assert first.status_code == 200, first.text
    before = _keys(pg_url, first.json()["user_id"])
    again = connect(client, token)
    assert again.status_code == 401 and "already used" in again.json()["detail"]
    assert _keys(pg_url, first.json()["user_id"]) == before, "a replay ends no key and issues none"
    assert all(token not in k for k in counter.spent), "only a hash of the token is kept"
    ttl = next(iter(counter.spent.values()))
    assert 0 < ttl <= 600 + 60, "kept until the token would have expired"


def test_a_stale_sign_in_is_refused(client, pg_url):
    now = int(time.time())
    r = connect(client, id_token(iat=now - 301, exp=now + 3000))
    assert r.status_code == 401 and "not fresh" in r.json()["detail"]
    assert _users(pg_url) == []


def test_a_token_issued_to_another_client_is_refused(client, pg_url):
    r = connect(client, id_token(azp=OTHER_CLIENT))
    assert r.status_code == 401 and "another client" in r.json()["detail"]
    assert connect(client, id_token(azp=None)).status_code == 200, "no azp: the audience alone decides"


def test_a_client_list_with_an_implausible_entry_verifies_nothing(client, pg_url, monkeypatch):
    for bad in (f"{BRAIN_CLIENT},brain-signin.apps.example", f"{BRAIN_CLIENT}, *", "x"):
        monkeypatch.setenv("VEXA_CONNECT_GOOGLE_CLIENT_IDS", bad)
        r = connect(client)
        assert r.status_code == 401 and "not configured" in r.json()["detail"], bad
    assert _users(pg_url) == []


def test_unknown_kids_reread_the_keys_at_most_once_a_minute(client, monkeypatch):
    fetches = []

    async def fetch(url):
        fetches.append(url)
        return JWKS

    idp_tokens.reset()
    monkeypatch.setattr(idp_tokens, "_fetch_json", fetch)
    for _ in range(5):
        assert connect(client, id_token(kid=secrets.token_hex(4))).status_code == 401
    assert len(fetches) == 1, "made-up kids do not each fetch the issuer's keys"
    idp_tokens._fetched_at[idp_tokens.GOOGLE_KEYS] -= idp_tokens.KEYS_MISS_REFETCH_S + 1
    assert connect(client, id_token(kid=secrets.token_hex(4))).status_code == 401
    assert len(fetches) == 2, "after a minute an unknown kid may re-read once (a key rotation)"
    assert connect(client).status_code == 200


def _user_row(pg_url, uid):
    return _rows(pg_url, "SELECT email, data::text FROM users WHERE id = :u", u=uid)[0]


def test_an_admin_named_by_the_deployment_is_refused_and_nothing_is_created(client, pg_url, monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", EMAIL)
    r = connect(client)
    assert r.status_code == 403 and r.json()["detail"] == connect_mod.ADMIN_REFUSED
    assert _users(pg_url) == []
    assert _rows(pg_url, "SELECT id FROM api_tokens") == []


def test_a_user_with_the_admin_role_is_refused_and_unchanged(client, pg_url):
    uid = client.post("/admin/users", headers=_admin(), json={"email": EMAIL}).json()["id"]
    _rows_exec(pg_url, "UPDATE users SET data = data || '{\"is_admin\": true}'::jsonb WHERE id = :u", u=uid)
    before = _user_row(pg_url, uid)
    r = connect(client)
    assert r.status_code == 403 and r.json()["detail"] == connect_mod.ADMIN_REFUSED
    assert _user_row(pg_url, uid) == before, "no binding, nothing about the admin changes"
    assert _keys(pg_url, uid) == []


def test_the_admin_account_is_refused_and_unchanged(client, pg_url):
    uid = client.post("/admin/users", headers=_admin(), json={"email": EMAIL}).json()["id"]
    stamp = json.dumps({admin_account.ACCOUNT_FIELD: admin_account.ACCOUNT_ADMIN})
    _rows_exec(pg_url, "UPDATE users SET data = data || CAST(:s AS jsonb) WHERE id = :u", s=stamp, u=uid)
    before = _user_row(pg_url, uid)
    r = connect(client)
    assert r.status_code == 403
    assert _user_row(pg_url, uid) == before and _keys(pg_url, uid) == []


def test_an_address_named_as_the_admin_account_is_not_created_by_connect(client, pg_url, monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_ACCOUNT", EMAIL)
    r = connect(client)
    assert r.status_code == 403 and r.json()["detail"] == connect_mod.ADMIN_REFUSED
    assert _users(pg_url) == []


def _rows_exec(pg_url, sql, **params):
    engine = create_engine(pg_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()


def test_the_same_token_respelled_is_still_used(client, pg_url):
    """Re-review 179468: PyJWT tolerates added padding, so `token + '=='` verified and hashed apart."""
    token = id_token()
    first = connect(client, token)
    assert first.status_code == 200, first.text
    uid = first.json()["user_id"]
    before = _keys(pg_url, uid)
    head, payload, sig = token.split(".")
    respelled = [token + "==", f"{head}.{payload}.{sig}==", f"{head}==.{payload}.{sig}",
                 f"{head}.{payload}=.{sig}", f"{head}.{payload}.{sig[:-1]}" + ("A" if sig[-1] != "A" else "Q")]
    for t in respelled:
        r = connect(client, t)
        assert r.status_code == 401, (t[-6:], r.status_code, r.text)
    assert _keys(pg_url, uid) == before, "no key ended, none issued"
