"""A second org's data is unreachable from the first (docker) — prepared for when there are two.

Today every install has one org and every account is in it, so nothing here changes an answer the
product gives. These tests pin the shape the later multi-org work must keep: every org-scoped read
in identity (`app/orgs.py`) answers another org's org, people and members exactly like ones that do
not exist. They create a second org by hand, the only way one can exist today.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from admin_api.app import db as app_db
from admin_api.app import orgs
from admin_api.app.main import create_app
from admin_api.schema.models import Base, DEFAULT_ORG_ID, User
from admin_api.schema.sync import ensure_schema_sync
from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _dispose_async_engine

pytestmark = requires_docker
OTHER = "org-b"


@pytest.fixture()
def two_orgs(pg_url, monkeypatch):
    monkeypatch.delenv("CRM_ORG_ID", raising=False)
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    ensure_schema_sync(engine, Base)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO orgs (id, name, policy_source, crm_org_id) "
                          "VALUES (:o, 'Org B', '_orgs/org-b', 'crm-b')"), {"o": OTHER})
        a = conn.execute(text("INSERT INTO users (email, max_concurrent_bots, data) "
                              "VALUES ('a@example.test', 3, '{}'::jsonb) RETURNING id")).scalar()
        b = conn.execute(text("INSERT INTO users (email, max_concurrent_bots, data, org_id) "
                              "VALUES ('b@example.test', 3, '{}'::jsonb, :o) RETURNING id"), {"o": OTHER}).scalar()
    engine.dispose()
    return {"a": int(a), "b": int(b)}


def _run(pg_async_url, fn):
    async def go():
        engine = create_async_engine(pg_async_url)
        try:
            async with AsyncSession(engine) as db:
                return await fn(db)
        finally:
            await engine.dispose()
    return asyncio.run(go())


def test_an_account_belongs_to_its_own_org_only(two_orgs, pg_async_url):
    async def fn(db):
        a = await db.get(User, two_orgs["a"])
        b = await db.get(User, two_orgs["b"])
        return (await orgs.membership(db, a, DEFAULT_ORG_ID), await orgs.membership(db, a, OTHER),
                await orgs.membership(db, b, OTHER), await orgs.membership(db, b, DEFAULT_ORG_ID))
    assert _run(pg_async_url, fn) == ("member", None, "member", None)


def test_another_orgs_record_reads_like_one_that_does_not_exist(two_orgs, pg_async_url):
    async def fn(db):
        a = await db.get(User, two_orgs["a"])
        return (await orgs.org_for_viewer(db, a, OTHER), await orgs.org_for_viewer(db, a, "no-such-org"),
                await orgs.org_for_viewer(db, a, DEFAULT_ORG_ID))
    other, missing, own = _run(pg_async_url, fn)
    assert other is None and missing is None
    assert own["id"] == DEFAULT_ORG_ID and own["role"] == "member"
    assert "crm-b" not in repr(own)


def test_an_orgs_people_never_include_another_orgs(two_orgs, pg_async_url):
    async def fn(db):
        return (await orgs.org_user_ids(db, DEFAULT_ORG_ID), await orgs.org_user_ids(db, OTHER),
                await orgs.user_in_org(db, DEFAULT_ORG_ID, two_orgs["b"]),
                await orgs.user_in_org(db, OTHER, two_orgs["a"]),
                await orgs.user_in_org(db, DEFAULT_ORG_ID, 10_000_000))
    default_ids, other_ids, b_from_a, a_from_b, unknown = _run(pg_async_url, fn)
    assert default_ids == [two_orgs["a"]] and other_ids == [two_orgs["b"]]
    assert b_from_a is None and a_from_b is None and unknown is None


def test_an_explicit_membership_is_the_only_way_across(two_orgs, pg_url, pg_async_url):
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO org_members (org_id, user_id, role) VALUES (:o, :u, 'member')"),
                     {"o": OTHER, "u": two_orgs["a"]})
    engine.dispose()

    async def fn(db):
        a = await db.get(User, two_orgs["a"])
        return await orgs.membership(db, a, OTHER), await orgs.org_user_ids(db, OTHER)
    role, ids = _run(pg_async_url, fn)
    assert role == "member" and ids == sorted([two_orgs["a"], two_orgs["b"]])


def test_deleting_an_account_drops_its_memberships(two_orgs, pg_url):
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO org_members (org_id, user_id, role) VALUES (:o, :u, 'admin')"),
                     {"o": OTHER, "u": two_orgs["b"]})
        conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": two_orgs["b"]})
        assert conn.execute(text("SELECT count(*) FROM org_members")).scalar() == 0
    engine.dispose()


@pytest.fixture()
def client(two_orgs, pg_async_url, monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def test_the_internal_door_answers_each_account_its_own_org(client, two_orgs):
    h = {"X-Internal-Secret": INTERNAL_SECRET}
    a = client.get(f"/internal/users/{two_orgs['a']}/org", headers=h).json()["org"]
    b = client.get(f"/internal/users/{two_orgs['b']}/org", headers=h).json()["org"]
    assert a["id"] == DEFAULT_ORG_ID and a["policy_source"] == "_global" and a["role"] == "member"
    assert b["id"] == OTHER and b["crm_org_id"] == "crm-b"
    assert "crm-b" not in repr(a) and OTHER not in repr(a)


def test_the_internal_doors_refuse_anyone_but_the_internal_tier(client, two_orgs):
    for path in (f"/internal/users/{two_orgs['a']}/org", f"/internal/orgs/{DEFAULT_ORG_ID}"):
        assert client.get(path).status_code == 403
        assert client.get(path, headers={"X-Internal-Secret": "wrong"}).status_code == 403
    assert client.get("/internal/orgs/no-such-org", headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 404


def test_dev_mode_does_not_open_the_doors(client, two_orgs, monkeypatch):
    """R1801-9: a named person's org is not readable without the secret, dev mode or not."""
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)   # dev mode with no secret named
    for path in (f"/internal/users/{two_orgs['b']}/org", f"/internal/orgs/{OTHER}"):
        r = client.get(path)
        assert r.status_code in (401, 403, 503) and "crm-b" not in r.text


def test_an_individual_gets_an_org_of_one_and_nobody_else_is_in_it(client, two_orgs, pg_url):
    h = {"X-Internal-Secret": INTERNAL_SECRET}
    a = two_orgs["a"]
    r = client.post(f"/internal/users/{a}/personal-org", headers=h)
    assert r.status_code == 200, r.text
    org = r.json()["org"]
    assert org["id"] == f"p-{a}" and org["kind"] == "personal" and org["role"] == "admin"
    again = client.post(f"/internal/users/{a}/personal-org", headers=h).json()["org"]
    assert again["id"] == org["id"]                                   # idempotent
    seen = client.get(f"/internal/users/{a}/org", headers=h).json()["org"]
    assert seen["id"] == f"p-{a}" and seen["kind"] == "personal"
    assert client.get(f"/internal/users/{two_orgs['b']}/org", headers=h).json()["org"]["id"] == OTHER
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        members = conn.execute(text("SELECT user_id FROM org_members WHERE org_id = :o"), {"o": f"p-{a}"}).scalars().all()
    engine.dispose()
    assert members == [a]
    assert client.post(f"/internal/users/{a}/personal-org").status_code == 403


def test_a_new_install_org_is_a_company_org(client):
    r = client.get("/internal/orgs/default", headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert r.json()["kind"] == "org"


# ── account delete takes the individual's org of one and their whole personal CRM with it ─────────

def _crm(monkeypatch, status_code):
    import httpx
    asked = []

    def handle(request):
        asked.append((request.method, request.url.path, request.headers.get("x-internal-secret")))
        return httpx.Response(status_code, json={"rows": 3})
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handle)}))
    monkeypatch.setenv("CRM_API_URL", "http://crm-api:8300")
    return asked


def _org_rows(pg_url, oid):
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        out = (conn.execute(text("SELECT count(*) FROM orgs WHERE id = :o"), {"o": oid}).scalar(),
               conn.execute(text("SELECT count(*) FROM org_members WHERE org_id = :o"), {"o": oid}).scalar())
    engine.dispose()
    return out


def test_deleting_an_individual_erases_their_crm_then_their_org(client, two_orgs, pg_url, monkeypatch):
    from test_stack_admin_api import _admin
    a = two_orgs["a"]
    client.post(f"/internal/users/{a}/personal-org", headers={"X-Internal-Secret": INTERNAL_SECRET})
    asked = _crm(monkeypatch, 200)
    r = client.delete(f"/admin/users/{a}", headers=_admin())
    assert r.status_code == 204, r.text
    assert asked == [("POST", f"/internal/orgs/p-{a}/erase", INTERNAL_SECRET)]
    assert _org_rows(pg_url, f"p-{a}") == (0, 0)
    assert client.get(f"/internal/users/{a}/org", headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 404


def test_if_the_crm_cannot_erase_nothing_is_deleted(client, two_orgs, pg_url, monkeypatch):
    from test_stack_admin_api import _admin
    a = two_orgs["a"]
    client.post(f"/internal/users/{a}/personal-org", headers={"X-Internal-Secret": INTERNAL_SECRET})
    _crm(monkeypatch, 503)
    assert client.delete(f"/admin/users/{a}", headers=_admin()).status_code == 502
    assert _org_rows(pg_url, f"p-{a}") == (1, 1)
    assert client.get(f"/internal/users/{a}/org", headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 200


def test_a_company_account_delete_never_asks_the_crm(client, two_orgs, monkeypatch):
    from test_stack_admin_api import _admin
    asked = _crm(monkeypatch, 200)
    assert client.delete(f"/admin/users/{two_orgs['b']}", headers=_admin()).status_code == 204
    assert asked == []
    assert _org_rows  # the company org itself is never removed by an account delete
