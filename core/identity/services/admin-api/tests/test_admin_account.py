"""THE ADMIN ACCOUNT (app/admin_account.py) — the one identity that writes `_global`.

Founder ruling 2026-10-11: *"admin is a special account that has global only and this being the only
one that has control to global"*. Identity owns the answer; these are its deny tests:

  * nothing configured → no admin account, for anybody;
  * an address named AFTER its row exists is never promoted (an everyday account stays everyday);
  * a row created while named is the admin account, launches no bot (cap 0 on every door);
  * removing the address revokes it; the stamp alone is not enough;
  * a worker is never the account — `person_admin_account` says whether its person is.

The pure half runs anywhere; the route half is the same testcontainers-PG harness as the other suites.
"""
import pytest

from admin_api.app import admin_account as aa
from admin_api.app import signin_allow as sa

ADMIN = "admin@example.com"
PERSON = "founder@example.com"


# ── the pure rule ─────────────────────────────────────────────────────────────────────────────

def test_unconfigured_means_nobody(monkeypatch):
    monkeypatch.delenv(aa.ENV_KEY, raising=False)
    assert aa.designated() == ([], [])
    stamped = {aa.ACCOUNT_FIELD: aa.ACCOUNT_ADMIN}
    assert aa.is_admin_account(ADMIN, stamped) is False
    assert aa.stamp_for_new_row(ADMIN, {}) == {}


def test_both_halves_are_required(monkeypatch):
    monkeypatch.setenv(aa.ENV_KEY, ADMIN)
    stamped = {aa.ACCOUNT_FIELD: aa.ACCOUNT_ADMIN}
    assert aa.is_admin_account(ADMIN, stamped) is True
    assert aa.is_admin_account("Admin@Example.com", stamped) is True          # case never matters
    assert aa.is_admin_account(ADMIN, {}) is False                            # named, never stamped
    assert aa.is_admin_account(ADMIN, {"is_admin": True}) is False            # the instance admin is not it
    assert aa.is_admin_account(PERSON, stamped) is False                      # stamped, not named
    monkeypatch.setenv(aa.ENV_KEY, "")
    assert aa.is_admin_account(ADMIN, stamped) is False                       # removing it revokes


def test_only_a_named_address_is_stamped_and_only_by_the_create_path(monkeypatch):
    monkeypatch.setenv(aa.ENV_KEY, ADMIN)
    assert aa.stamp_for_new_row(ADMIN, {"x": 1}) == {"x": 1, aa.ACCOUNT_FIELD: aa.ACCOUNT_ADMIN}
    assert aa.stamp_for_new_row(PERSON, {"x": 1}) == {"x": 1}


@pytest.mark.parametrize("raw", ["*", "@example.com", "not-an-address", ",".join(
    f"a{i}@example.com" for i in range(aa.MAX_ADDRESSES + 1))])
def test_a_malformed_designation_refuses_the_boot(monkeypatch, raw):
    monkeypatch.setenv(aa.ENV_KEY, raw)
    problems = aa.boot_problems()
    assert problems and all(p.startswith(aa.ENV_KEY) for p in problems)


def test_a_named_admin_account_may_sign_in_to_be_created():
    ok, why = sa.decide(ADMIN, user_exists=False, is_admin=False, admin_claimed=True, allow=[],
                        admin_accounts=[ADMIN])
    assert (ok, why) == (True, sa.WHY_ADMIN_ACCOUNT)
    # …and nobody else rides on it
    assert sa.decide(PERSON, user_exists=False, is_admin=False, admin_claimed=True, allow=[],
                     admin_accounts=[ADMIN]) == (False, sa.WHY_NOT_ALLOWED)


# ── the routes, on a real database ──────────────────────────────────────────────────────────

try:
    from conftest import requires_docker
except Exception:  # pragma: no cover — the harness is always importable in this suite
    requires_docker = pytest.mark.skip(reason="no harness")


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
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
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    monkeypatch.delenv(aa.ENV_KEY, raising=False)
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _h():
    from test_stack_admin_api import INTERNAL_SECRET
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _mk(client, email):
    from test_stack_admin_api import _admin
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def _token(client, uid):
    from test_stack_admin_api import _admin
    return client.post(f"/admin/users/{uid}/tokens", headers=_admin()).json()["token"]


def _who(client, tok):
    r = client.post("/internal/validate", headers=_h(), json={"token": tok})
    assert r.status_code == 200
    return r.json()


@requires_docker
def test_unconfigured_nobody_is_the_admin_account(client):
    uid = _mk(client, ADMIN)
    assert _who(client, _token(client, uid))["admin_account"] is False
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_h()).json()["admin_account"] is False


@requires_docker
def test_an_everyday_account_is_never_promoted(client, monkeypatch):
    uid = _mk(client, PERSON)                         # the founder's own account, already here
    monkeypatch.setenv(aa.ENV_KEY, PERSON)            # …and then named
    assert _mk(client, PERSON) == uid                 # find-or-create finds, never re-stamps
    who = _who(client, _token(client, uid))
    assert who["admin_account"] is False and who["max_concurrent"] > 0
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_h()).json()["admin_account"] is False


@requires_docker
def test_the_named_account_is_created_as_the_admin_account_and_launches_no_bot(client, monkeypatch):
    monkeypatch.setenv(aa.ENV_KEY, ADMIN)
    uid = _mk(client, ADMIN)
    who = _who(client, _token(client, uid))
    assert who["admin_account"] is True and who["is_admin"] is False
    assert who["max_concurrent"] == 0
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_h()).json()["admin_account"] is True
    assert client.get(f"/internal/users/{uid}/bot-context", headers=_h()).json()["max_concurrent"] == 0
    # an everyday account beside it is unchanged
    other = _who(client, _token(client, _mk(client, PERSON)))
    assert other["admin_account"] is False and other["max_concurrent"] > 0


@requires_docker
def test_removing_the_address_revokes_the_account(client, monkeypatch):
    monkeypatch.setenv(aa.ENV_KEY, ADMIN)
    uid = _mk(client, ADMIN)
    monkeypatch.setenv(aa.ENV_KEY, "")
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_h()).json()["admin_account"] is False
    assert _who(client, _token(client, uid))["admin_account"] is False


@requires_docker
def test_the_signin_door_admits_the_named_address(client, monkeypatch):
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "someone@example.org")   # a closed instance
    monkeypatch.setenv(aa.ENV_KEY, ADMIN)
    r = client.post("/internal/signin-admission", headers=_h(), json={"email": ADMIN})
    assert r.json() == {"admitted": True, "why": "admin-account"}
