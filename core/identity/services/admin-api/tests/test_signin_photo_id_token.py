"""A provider photo comes only from the person's OWN verified sign-in (Vexa-ai/vexa#1783, UI 15g).

Review follow-up: holding INTERNAL_API_SECRET must not let a caller put any picture on anybody. So
the internal sign-in door takes no `url` at all. For Google and OIDC it takes the sign-in's raw ID
token, verifies it against the issuer's keys (signature, issuer, audience = our client id, expiry),
checks that its subject IS the provider identity bound to this account, and only then reads
`picture` from the verified claims and fetches it through the outbound-URL guard.

Offline: a generated RSA key, a fake JWKS/discovery fetch, a recording picture fetch.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from admin_api.app import db as app_db_mod  # noqa: F401 — imported for the override below
from admin_api.app import idp_tokens
from admin_api.app import main as main_mod
from admin_api.app import person_photo_doors as doors
from admin_api.app import person_photos as pp
from admin_api.app.db import get_db
from admin_api.schema.models import User
from photo_samples import SVG, jpeg

SECRET = "internal-secret-for-the-id-token-door"
GOOGLE_CLIENT = "google-client-id.apps.example"
OIDC_ISSUER = "https://idp.example.test/realms/corp"
OIDC_CLIENT = "vexa-terminal"
PICTURE = "https://lh3.googleusercontent.com/a/the-persons-picture=s96-c"
OIDC_PICTURE = "https://idp.example.test/pictures/ana.jpg"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key, kid):
    j = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**j, "kid": kid, "alg": "RS256", "use": "sig"}


JWKS = {"keys": [_jwk(KEY, "k1")]}


def token(claims, *, key=KEY, kid="k1"):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def google_claims(**over):
    now = int(time.time())
    return {"iss": "https://accounts.google.com", "aud": GOOGLE_CLIENT, "sub": "1234567890",
            "email": "ana@corp.example", "email_verified": True, "picture": PICTURE,
            "iat": now, "exp": now + 600, **over}


def oidc_claims(**over):
    now = int(time.time())
    return {"iss": OIDC_ISSUER, "aud": OIDC_CLIENT, "sub": "ZiV3uFjC0b8x", "email": "ana@corp.example",
            "picture": OIDC_PICTURE, "iat": now, "exp": now + 600, **over}


def oidc_subject(iss, sub):
    return "oidc:" + hashlib.sha256(f"{iss.rstrip('/')}\n{sub}".encode()).hexdigest()


SHARED: dict = {}


class _Mem:
    objects = object()

    def __init__(self, db=None):
        pass

    async def rows(self, user_id):
        return {k: dict(v) for k, v in SHARED.get(user_id, {}).items()}

    async def put(self, user_id, source, meta, body):
        SHARED.setdefault(user_id, {})[source] = {**meta, "_body": body}

    async def drop(self, user_id, source):
        SHARED.get(user_id, {}).pop(source, None)

    async def read(self, user_id, source, meta):
        return SHARED.get(user_id, {}).get(source, {}).get("_body")


class _Result:
    def __init__(self, user):
        self._user = user

    def scalars(self):
        return self

    def first(self):
        return self._user

    def scalar_one_or_none(self):
        return self._user


class _Session:
    def __init__(self, user):
        self._user = user

    async def execute(self, stmt):
        return _Result(self._user)


@pytest.fixture()
def person():
    return User(id=47, email="ana@corp.example", data={"provider_subjects": {
        "google": "google:1234567890", "oidc": oidc_subject(OIDC_ISSUER, "ZiV3uFjC0b8x")}})


@pytest.fixture()
def net():
    return {"asked": [], "answers": {PICTURE: jpeg(gps=True), OIDC_PICTURE: jpeg()}}


@pytest.fixture()
def client(monkeypatch, person, net):
    SHARED.clear()
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-id-token-door")
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("VEXA_PERSON_GRAVATAR", "false")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", GOOGLE_CLIENT)
    monkeypatch.setenv("VEXA_OIDC_ISSUER", OIDC_ISSUER)
    monkeypatch.setenv("VEXA_OIDC_CLIENT_ID", OIDC_CLIENT)
    idp_tokens.reset()

    async def keys(url):
        if url == f"{OIDC_ISSUER}/.well-known/openid-configuration":
            return {"issuer": OIDC_ISSUER, "jwks_uri": f"{OIDC_ISSUER}/keys"}
        return JWKS

    async def fetch(url, headers=None):
        net["asked"].append(url)
        return net["answers"].get(url)

    monkeypatch.setattr(idp_tokens, "_fetch_json", keys)
    monkeypatch.setattr(doors, "DbStore", _Mem)
    monkeypatch.setattr(pp, "fetch_image", fetch)
    app = main_mod.create_app()

    async def _db():
        yield _Session(person)

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c


def _put(client, body):
    return client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET}, json=body)


# ── the verified path ────────────────────────────────────────────────────────────────────────────
def test_a_google_signin_photo_comes_from_the_verified_id_token(client, net):
    r = _put(client, {"provider": "google", "id_token": token(google_claims())})
    assert r.status_code == 200 and r.json()["provider"] == "stored", r.text
    assert net["asked"] == [PICTURE]
    assert SHARED[47]["provider"]["provider"] == "google"


def test_an_oidc_signin_photo_comes_from_the_verified_id_token(client, net):
    r = _put(client, {"provider": "oidc", "id_token": token(oidc_claims())})
    assert r.json()["provider"] == "stored", r.text
    assert net["asked"] == [OIDC_PICTURE]


# ── (a) a url field is refused ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body", [
    {"provider": "google", "url": PICTURE},
    {"provider": "oidc", "url": "https://anything.example/face.jpg"},
    {"provider": "google", "id_token": "x", "url": PICTURE},
    {"provider": "microsoft", "data": "data:image/jpeg;base64,AAAA"},
])
def test_a_caller_supplied_url_or_bytes_is_refused(client, net, body):
    assert _put(client, body).status_code == 422
    assert net["asked"] == [] and SHARED == {}


# ── (b) an ID token that is not this person's verified sign-in sets nothing ──────────────────────
@pytest.mark.parametrize("provider,tok", [
    ("google", token(google_claims(sub="999"))),                                   # another subject
    ("google", token(google_claims(), key=OTHER_KEY)),                              # forged signature
    ("google", token(google_claims(aud="another-client"))),                         # wrong audience
    ("google", token(google_claims(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600))),  # expired
    ("google", token(google_claims(iss="https://evil.example"))),                   # wrong issuer
    ("google", token(google_claims(), kid="unknown")),                              # unknown key
    ("google", "not.a.jwt"),
    ("oidc", token(oidc_claims(sub="somebody-else"))),
    ("oidc", token(oidc_claims(aud=GOOGLE_CLIENT))),
    ("oidc", token(oidc_claims(), key=OTHER_KEY)),
    ("oidc", token(google_claims())),                                               # a Google token at the OIDC door
])
def test_an_id_token_that_is_not_this_persons_verified_signin_fetches_nothing(client, net, provider, tok):
    r = _put(client, {"provider": provider, "id_token": tok})
    assert r.status_code == 200 and r.json()["provider"] == "refused", r.text
    assert net["asked"] == [] and SHARED == {}


def test_an_unsigned_token_is_refused(client, net):
    unsigned = jwt.encode(google_claims(), key=None, algorithm="none")
    assert _put(client, {"provider": "google", "id_token": unsigned}).json()["provider"] == "refused"
    assert net["asked"] == []


def test_without_the_providers_client_id_nothing_is_verified_or_fetched(client, net, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    assert _put(client, {"provider": "google", "id_token": token(google_claims())}).json()["provider"] == "refused"
    assert net["asked"] == []


def test_an_account_not_bound_to_that_provider_gets_nothing(client, net, person):
    person.data = {"provider_subjects": {}}
    assert _put(client, {"provider": "google", "id_token": token(google_claims())}).json()["provider"] == "refused"
    assert net["asked"] == []


# ── (c) a verified picture that is not a safe image is not stored ────────────────────────────────
@pytest.mark.parametrize("answer", [SVG, b"<html>not an image</html>", None])
def test_a_verified_picture_that_is_not_an_image_is_not_stored(client, net, answer):
    net["answers"][PICTURE] = answer
    r = _put(client, {"provider": "google", "id_token": token(google_claims())})
    assert r.json()["provider"] == "refused" and SHARED == {}


def test_a_verified_oidc_picture_on_plain_http_is_not_fetched(client, net):
    r = _put(client, {"provider": "oidc", "id_token": token(oidc_claims(picture="http://idp.example.test/p.jpg"))})
    assert r.json()["provider"] in ("refused", "none") and net["asked"] == [] and SHARED == {}


@pytest.mark.parametrize("url", ["https://10.0.0.5/p.jpg", "https://127.0.0.1/p.jpg", "https://169.254.169.254/latest",
                                 "https://[::1]/p.jpg", "https://localhost/p.jpg", "https://admin-api/p.jpg"])
def test_the_outbound_guard_refuses_a_private_picture_address(url):
    assert asyncio.run(pp.fetch_image(url)) is None


def test_a_redirect_to_a_private_address_is_refused():
    from admin_api.app.ssrf import build_pinned_transport

    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": "https://10.0.0.1/internal.jpg"})
        return httpx.Response(200, content=jpeg())

    pinned = build_pinned_transport(inner=httpx.MockTransport(handler))
    assert asyncio.run(pp.fetch_image("https://93.184.216.34/p.jpg", transport=pinned)) is None
    assert not any("10.0.0.1" in u for u in seen)


def test_an_oversize_picture_is_not_read_past_the_cap():
    big = b"\xff\xd8\xff" + b"\x00" * (pp.MAX_BYTES + 10)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=big))
    assert asyncio.run(pp.fetch_image("https://93.184.216.34/p.jpg", transport=transport)) is None
