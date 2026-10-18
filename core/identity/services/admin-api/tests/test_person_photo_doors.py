"""The doors to a person's photo (Vexa-ai/vexa#1783, UI 15g): only the person sets or clears their
own; a worker acting for them is refused; the bytes leave only over the internal tier; a sign-in
captures the provider's picture.

Offline tests stub the DB session and the store; the last test is a Postgres round trip (skips
without docker — run it on bbb).
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from admin_api import delegation
from admin_api.app import delegation_revocation as revocation
from admin_api.app import main as main_mod
from admin_api.app import person_photo_doors as doors
from admin_api.app import person_photos as pp
from admin_api.app import photo_storage
from admin_api.app.db import get_db
from admin_api.schema.models import User
from photo_samples import SVG, has_metadata, jpeg, png

DLG = "delegation-secret-for-the-photo-doors"
SECRET = "internal-secret-for-the-photo-doors"
GOOGLE = "https://lh3.googleusercontent.com/a/the-persons-picture=s96-c"


class _Result:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user

    def scalar_one(self):
        return self._user

    def scalars(self):
        return self

    def first(self):
        return self._user


class _Session:
    def __init__(self, users):
        self._users = users

    async def execute(self, stmt):
        params = stmt.compile().params
        uid = next((v for v in params.values() if isinstance(v, int)), None)
        return _Result(self._users.get(uid))

    def add(self, obj):
        pass

    async def commit(self):
        pass


SHARED: dict = {}
BYTES: dict = {}


class _Mem:
    objects = object()          # a configured object store

    def __init__(self, db=None):
        pass

    async def rows(self, user_id):
        return {k: dict(v) for k, v in SHARED.get(user_id, {}).items()}

    async def put(self, user_id, source, meta, body):
        SHARED.setdefault(user_id, {})[source] = dict(meta)
        BYTES[(user_id, source)] = body

    async def drop(self, user_id, source):
        SHARED.get(user_id, {}).pop(source, None)
        BYTES.pop((user_id, source), None)

    async def read(self, user_id, source, meta):
        return BYTES.get((user_id, source))


class _NoObjects(_Mem):
    objects = None


OID = "0f0f0f0f-1111-2222-3333-444444444444"


@pytest.fixture()
def person():
    return User(id=47, email="person@example.com",
                data={"provider_subjects": {"microsoft": f"microsoft:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee:{OID}"}})


@pytest.fixture()
def asked():
    return []


@pytest.fixture()
def client(monkeypatch, person, asked):
    SHARED.clear()
    BYTES.clear()
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-photo-doors")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", DLG)
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")

    async def _admitted(jti):
        return True

    async def _fetch(url, headers=None):
        asked.append(url)
        if url == pp.GRAPH_ME:
            return json.dumps({"id": OID}).encode() if (headers or {}).get("Authorization") == "Bearer good" else b"{}"
        return jpeg(gps=True) if url in (GOOGLE, pp.GRAPH_PHOTO) else None

    monkeypatch.setattr(revocation, "is_admitted", _admitted)
    monkeypatch.setattr(doors, "DbStore", _Mem)
    monkeypatch.setattr(pp, "fetch_image", _fetch)
    app = main_mod.create_app()

    async def _db():
        yield _Session({47: person})

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def as_person(client, person):
    client.app.dependency_overrides[main_mod.get_current_person] = lambda: person
    return client


def _worker(regime="human", subject="47"):
    return {"X-API-Key": delegation.mint_delegation(DLG, subject=subject, regime=regime,
                                                     workspaces="*" if regime == "human" else ["47"],
                                                     unit="agent-47-chat-main")}


def _b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


def _routes(app):
    out = set()

    def walk(routes):
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            for m in getattr(r, "methods", set()) or set():
                out.add((m, r.path))
    walk(app.routes)
    return out


def test_the_photo_doors_are_routed(client):
    routes = _routes(client.app)
    for row in [("GET", "/user/photo"), ("PUT", "/user/photo"), ("DELETE", "/user/photo"),
                ("GET", "/internal/users/{user_id}/photo"), ("PUT", "/internal/users/{user_id}/signin-photo")]:
        assert row in routes


# ── only the person changes their own photo ──────────────────────────────────────────────────────
def test_the_person_uploads_sees_and_removes_their_photo(as_person):
    c = as_person
    assert c.get("/user/photo").json()["photo"] is None
    r = c.put("/user/photo", json={"image": _b64(png())})
    assert r.status_code == 200, r.text
    assert r.json()["photo"]["source"] == "upload" and r.json()["subject"] == "47"
    assert c.get("/user/photo").json()["photo"]["source"] == "upload"
    assert c.delete("/user/photo").json()["photo"] is None


def test_an_svg_or_junk_upload_is_refused(as_person):
    assert as_person.put("/user/photo", json={"image": _b64(SVG)}).status_code == 422
    assert as_person.put("/user/photo", json={"image": "not base64 at all !!"}).status_code == 422
    assert SHARED == {}


def test_an_upload_is_stored_without_its_metadata(as_person):
    assert as_person.put("/user/photo", json={"image": _b64(jpeg(gps=True))}).status_code == 200
    assert has_metadata(BYTES[(47, "upload")]) == []


# ── no route lets one person touch another's photo (review item 4) ──────────────────────────────
def test_put_naming_another_user_is_refused_and_their_photo_untouched(as_person):
    SHARED[48] = {"upload": {"content_type": "image/png", "sha256": "c" * 64}}
    BYTES[(48, "upload")] = png()
    for extra in ({"user_id": 48}, {"subject": "48"}, {"id": 48}):
        r = as_person.put("/user/photo", json={"image": _b64(png()), **extra})
        assert r.status_code == 422, extra
    assert SHARED[48]["upload"]["sha256"] == "c" * 64 and BYTES[(48, "upload")] == png()
    assert 47 not in SHARED


def test_delete_naming_another_user_removes_only_the_callers_own(as_person):
    SHARED[48] = {"upload": {"content_type": "image/png", "sha256": "c" * 64}}
    BYTES[(48, "upload")] = png()
    as_person.put("/user/photo", json={"image": _b64(png())})
    r = as_person.request("DELETE", "/user/photo?user_id=48", json={"user_id": 48})
    assert r.status_code == 200 and r.json()["subject"] == "47"
    assert "upload" in SHARED[48] and "upload" not in SHARED.get(47, {})


def test_without_an_object_store_a_photo_cannot_be_set(as_person, monkeypatch):
    monkeypatch.setattr(doors, "DbStore", _NoObjects)
    app = main_mod.create_app()
    app.dependency_overrides.update(as_person.app.dependency_overrides)
    with TestClient(app) as c:
        assert c.put("/user/photo", json={"image": _b64(png())}).status_code == 503


@pytest.mark.parametrize("regime", ["human", "autonomous"])
def test_a_worker_acting_for_the_person_cannot_set_or_clear_their_photo(client, regime):
    """An agent may suggest a photo; it never sets one — not even with the person in the loop."""
    assert client.put("/user/photo", json={"image": _b64(png())}, headers=_worker(regime)).status_code == 403
    assert client.delete("/user/photo", headers=_worker(regime)).status_code == 403
    assert SHARED == {}


def test_a_worker_may_read_which_photo_shows(client):
    assert client.get("/user/photo", headers=_worker()).status_code == 200


# ── the internal tier ────────────────────────────────────────────────────────────────────────────
def test_the_bytes_leave_only_with_the_internal_secret(client, monkeypatch):
    SHARED[47] = {"upload": {"content_type": "image/png", "sha256": "a" * 64}}
    BYTES[(47, "upload")] = png()
    assert client.get("/internal/users/47/photo").status_code == 403
    assert client.get("/internal/users/47/photo", headers={"X-Internal-Secret": "wrong"}).status_code == 403
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET")
    assert client.get("/internal/users/47/photo").status_code == 503, "no dev-mode bypass on a per-person door"


def test_the_internal_door_serves_the_winning_photo_with_its_sniffed_type(client):
    SHARED[47] = {"upload": {"content_type": "image/png", "sha256": "a" * 64},
                  "provider": {"content_type": "image/jpeg", "sha256": "b" * 64}}
    BYTES[(47, "upload")], BYTES[(47, "provider")] = png(), jpeg()
    r = client.get("/internal/users/47/photo", headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200 and r.content == png()
    assert r.headers["content-type"] == "image/png" and r.headers["x-photo-source"] == "upload"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/internal/users/48/photo", headers={"X-Internal-Secret": SECRET}).status_code == 404


def test_a_google_signin_with_a_caller_url_is_refused(client, asked):
    """The picture's address comes only from the verified ID token (test_signin_photo_id_token.py)."""
    r = client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET},
                   json={"provider": "google", "url": GOOGLE})
    assert r.status_code == 422 and asked == [] and SHARED == {}


def test_the_internal_door_takes_no_bytes(client, asked):
    """Review item 2: holding the internal secret is not a way to put a picture on somebody."""
    r = client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET},
                   json={"provider": "microsoft", "data": "data:image/jpeg;base64," + _b64(jpeg())})
    assert r.status_code == 422
    assert SHARED == {} and asked == []


def test_a_microsoft_signin_lets_identity_fetch_graph_for_the_bound_account(client, asked):
    r = client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET},
                   json={"provider": "microsoft", "access_token": "good"})
    assert r.status_code == 200 and r.json()["provider"] == "stored", r.text
    assert asked == [pp.GRAPH_ME, pp.GRAPH_PHOTO]
    assert has_metadata(BYTES[(47, "provider")]) == []


def test_a_microsoft_token_that_is_not_the_bound_account_sets_nothing(client, asked, monkeypatch):
    monkeypatch.setenv("VEXA_PERSON_GRAVATAR", "false")
    r = client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET},
                   json={"provider": "microsoft", "access_token": "someone-elses"})
    assert r.json()["provider"] == "refused" and SHARED == {}
    assert pp.GRAPH_PHOTO not in asked


def test_an_emailed_link_signin_asks_gravatar_only_when_switched_on(client, asked, monkeypatch):
    monkeypatch.setenv("VEXA_PERSON_GRAVATAR", "false")
    client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET}, json={})
    assert asked == []
    monkeypatch.setenv("VEXA_PERSON_GRAVATAR", "true")
    client.put("/internal/users/47/signin-photo", headers={"X-Internal-Secret": SECRET}, json={})
    assert asked == [pp.gravatar_url("person@example.com")]


def test_the_signin_door_needs_the_secret(client, asked):
    assert client.put("/internal/users/47/signin-photo", json={"provider": "google", "id_token": "x"}).status_code == 403
    assert asked == []


# ── Postgres round trip (bbb) ────────────────────────────────────────────────────────────────────
from conftest import requires_docker  # noqa: E402


class FakeObjects:
    """The object store, in memory, recording every call."""

    def __init__(self):
        self.data: dict = {}

    def put(self, key, data, content_type):
        photo_storage.check_key(key)
        self.data[key] = bytes(data)

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)

    def delete_person(self, user_id):
        gone = [k for k in self.data if k.startswith(f"person-photos/{user_id}/")]
        for k in gone:
            self.data.pop(k)
        return len(gone)


def test_the_s3_adapter_keeps_to_its_prefix_and_deletes_a_person():
    calls = []

    class Client:
        def put_object(self, **kw):
            calls.append(("put", kw["Key"]))

        def delete_object(self, **kw):
            calls.append(("delete", kw["Key"]))

        def list_objects_v2(self, **kw):
            calls.append(("list", kw["Prefix"]))
            return {"Contents": [{"Key": "person-photos/5/upload-0123456789abcdef.png"}], "IsTruncated": False}

        def delete_objects(self, **kw):
            calls.append(("delete_objects", [o["Key"] for o in kw["Delete"]["Objects"]]))

    s3 = photo_storage.S3Objects(bucket="vexa", client=Client())
    key = photo_storage.key_for(5, "upload", "0123456789abcdef" * 4, "image/png")
    s3.put(key, png(), "image/png")
    assert s3.delete_person(5) == 1
    assert calls == [("put", "person-photos/5/upload-0123456789abcdef.png"), ("list", "person-photos/5/"),
                     ("delete_objects", ["person-photos/5/upload-0123456789abcdef.png"])]
    for bad in ("recordings/x.png", "person-photos/5/../6/upload-0123456789abcdef.png", "person-photos/5/x.svg"):
        with pytest.raises(ValueError):
            s3.put(bad, b"x", "image/png")


def test_the_store_is_off_without_an_endpoint_or_key(monkeypatch):
    photo_storage.reset_for_tests()
    for k in ("S3_ENDPOINT", "MINIO_ENDPOINT", "S3_ACCESS_KEY", "MINIO_ACCESS_KEY", "S3_SECRET_KEY", "MINIO_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert photo_storage.from_env() is None
    photo_storage.reset_for_tests()
    monkeypatch.setenv("MINIO_ENDPOINT", "storage:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")
    assert isinstance(photo_storage.from_env(), photo_storage.S3Objects)
    photo_storage.reset_for_tests()


# ── Postgres round trip (bbb) ────────────────────────────────────────────────────────────────────
from conftest import requires_docker  # noqa: E402


@requires_docker
def test_db_store_keeps_a_key_not_bytes_and_user_delete_takes_the_objects(pg_url, pg_async_url, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from admin_api.app import db as app_db
    from admin_api.schema.models import Base
    from admin_api.schema.sync import ensure_schema_sync

    eng = create_engine(pg_url)
    Base.metadata.drop_all(eng)
    ensure_schema_sync(eng, Base)
    cols = {c["name"] for c in inspect(eng).get_columns("person_photos")}
    assert "body" not in cols and "object_key" in cols
    with eng.begin() as cx:
        cx.execute(text("INSERT INTO users (id, email, data) VALUES (5, 'five@example.com', '{}')"))
    objects = FakeObjects()
    photo_storage.set_for_tests(objects)
    app_db.configure(pg_async_url)

    async def go():
        async with app_db.session() as s:
            store = doors.DbStore(s, objects)
            await pp.upload(store, 5, jpeg(gps=True))
            rows = await store.rows(5)
            assert pp.pick(rows)[0] == "upload" and rows["upload"]["object_key"] in objects.data
            assert has_metadata(await store.read(5, "upload", rows["upload"])) == []
            await pp.remove_upload(store, 5)
            assert pp.pick(await store.rows(5)) is None and objects.data == {}
            await pp.upload(store, 5, png())

    asyncio.run(go())
    app_db.configure(pg_async_url)   # a fresh pool: the one above is bound to asyncio.run's closed loop
    assert len(objects.data) == 1
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-photo-doors")
    with TestClient(main_mod.create_app()) as c:
        assert c.delete("/admin/users/5", headers={"X-Admin-API-Key": "admin-token-for-the-photo-doors"}).status_code == 204
    assert objects.data == {}
    with eng.begin() as cx:
        assert cx.execute(text("SELECT count(*) FROM person_photos WHERE user_id = 5")).scalar() == 0
    photo_storage.reset_for_tests()
    eng.dispose()
