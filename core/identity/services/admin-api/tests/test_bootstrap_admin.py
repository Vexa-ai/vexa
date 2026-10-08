"""First-run bootstrap admin — /internal/instance + /internal/bootstrap-admin + the is_admin
surfacing on /internal/validate (the terminal admin gate's input).

Contract (first-run onboarding design, 2026-07-09): a fresh instance has NO admin unless
VEXA_ADMIN_EMAILS names them; the role is claimed exactly once (advisory-lock serialized), by whoever
`signin_allow.may_claim` permits; later claims never succeed.
The role lives in users.data["is_admin"] — no schema migration.

Same testcontainers-PG harness as the other suites (skips without docker).
"""
import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _mk_user(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


_throwaway = itertools.count()


def _claim_code(client):
    """A live admin claim code, the way an operator gets one on a running instance: releasing the
    role (here from a throwaway account that never held it) hands the instance back to first run and
    answers a fresh code. At boot the same code is written to the admin-api log."""
    uid = _mk_user(client, f"code-{next(_throwaway)}-test@vexa.ai")
    code = client.post("/internal/release-admin", headers=_internal(), json={"user_id": uid}).json()["claim_code"]
    client.delete(f"/admin/users/{uid}", headers=_admin())
    return code


def _claim(client, user_id, code=None):
    body = {"user_id": user_id} if code is None else {"user_id": user_id, "claim_code": code}
    return client.post("/internal/bootstrap-admin", headers=_internal(), json=body)


def test_instance_and_bootstrap_gate_fail_closed(client):
    # internal edge only — no/wrong secret rejected
    assert client.get("/internal/instance").status_code == 403
    assert client.post("/internal/bootstrap-admin",
                       headers={"X-Internal-Secret": "wrong"},
                       json={"user_id": 1}).status_code == 403


def test_first_claim_wins_then_idempotent(client):
    a = _mk_user(client, "first-test@vexa.ai")
    b = _mk_user(client, "second-test@vexa.ai")

    # fresh instance: no admin
    r = client.get("/internal/instance", headers=_internal())
    assert r.status_code == 200
    assert r.json() == {"admin_exists": False}

    # no code, no claim
    assert _claim(client, a).json() == {"claimed": False, "admin_exists": False, "why": "bad-code"}
    # the sign-in that holds the code claims
    code = _claim_code(client)
    r = _claim(client, a, code)
    assert r.status_code == 200 and r.json() == {"claimed": True, "admin_exists": True, "why": "claimed"}

    # instance now has an admin — and that is the whole instance state: there is no company-layer
    # gate to report any more (founder ruling 2026-10-08)
    assert client.get("/internal/instance", headers=_internal()).json() == {"admin_exists": True}

    # a later user never claims; the admin re-claiming is a harmless no-op
    later = {"claimed": False, "admin_exists": True, "why": "admin-exists"}
    assert _claim(client, b, code).json() == later
    assert _claim(client, a, code).json() == later


def test_bootstrap_unknown_user_404(client):
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": 99999}).status_code == 404
    # a body without a user is not the signin.v1 AdminClaimRequest shape
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={}).status_code == 422


def test_validate_surfaces_is_admin(client):
    uid = _mk_user(client, "admin-test@vexa.ai")
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]

    # before the claim: not an admin
    r = client.post("/internal/validate", headers=_internal(), json={"token": tok})
    assert r.status_code == 200 and r.json()["is_admin"] is False

    _claim(client, uid, _claim_code(client))
    r = client.post("/internal/validate", headers=_internal(), json={"token": tok})
    assert r.json()["is_admin"] is True


def test_setup_settings_key(client):
    # the wizard's durable step state rides the platform-settings CRUD under key "setup"
    r = client.put("/internal/settings/setup", headers=_internal(),
                   json={"models": "done", "transcription": "skipped", "completed": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["value"] == {"models": "done", "transcription": "skipped", "completed": "true"}
    # partial clear semantics hold
    r = client.put("/internal/settings/setup", headers=_internal(), json={"transcription": ""})
    assert r.json()["value"] == {"models": "done", "completed": "true"}


# ── no company-layer gate (founder ruling 2026-10-08) ──────────────────────────────────────────

def test_the_global_setup_row_is_written_but_never_read(client):
    """The key stays writable so an older writer does not 400, and nothing reads it: whatever it
    says, the instance state is only whether an admin exists."""
    client.put("/internal/settings/global_setup", headers=_internal(),
               json={"state": "missing", "company": "Acme GmbH"})
    assert client.get("/internal/instance", headers=_internal()).json() == {"admin_exists": False}
    assert client.get("/admin/instance", headers=_admin()).json() == {"admin_exists": False}
    assert client.get("/admin/instance").status_code in (401, 403)


def test_the_gate_doors_are_gone(client):
    """`/internal/signin-allowed` refused everyone but the admin while `_global` was unwritten, and
    `PUT /admin/instance/global-setup` was the operator door that lifted it. Neither exists."""
    assert client.post("/internal/signin-allowed", headers=_internal(),
                       json={"email": "x@y.z"}).status_code in (404, 405)
    assert client.put("/admin/instance/global-setup", headers=_admin(),
                      json={"company": "Acme"}).status_code in (404, 405)


def test_signin_admission_against_real_postgres(client, monkeypatch):
    """Vexa-ai/vexa#1783, end to end on a real schema: while no admin exists and nothing is
    configured, only the sign-in that holds the claim code is admitted (it is the claim); a
    configured allow-list closes that door; once the claim lands, a stranger is refused, the admin
    and every existing user are admitted, and the allow-list (env + settings row) admits the rest.
    The offline twin of this is tests/test_signin_allow.py."""
    def ask(email, code=None):
        body = {"email": email} if code is None else {"email": email, "claim_code": code}
        return client.post("/internal/signin-admission", headers=_internal(), json=body).json()

    code = _claim_code(client)
    assert ask("first@anywhere.net") == {"admitted": False, "why": "not-allowed"}
    assert ask("first@anywhere.net", code) == {"admitted": True, "why": "claim-code"}
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@seeded.example")
    assert ask("first@anywhere.net", code) == {"admitted": False, "why": "not-allowed"}

    boss = _mk_user(client, "boss-test@vexa.ai")
    _mk_user(client, "Member-Test@vexa.ai")
    # with the allow-list in force, an address not on it may not be the first admin, code or not…
    assert _claim(client, boss, code).json()["why"] == "not-allowed"
    # …and with it lifted, the claim lands
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW")
    assert _claim(client, boss, code).json()["claimed"] is True
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@seeded.example")

    assert ask("stranger@anywhere.net") == {"admitted": False, "why": "not-allowed"}
    assert ask("BOSS-test@vexa.ai") == {"admitted": True, "why": "admin"}
    assert ask("member-test@vexa.ai") == {"admitted": True, "why": "existing-user"}
    assert ask("anna@seeded.example") == {"admitted": True, "why": "allow-list"}

    assert client.put("/internal/settings/signin", headers=_internal(),
                      json={"allow": "Stranger@anywhere.net"}).status_code == 200
    assert ask("stranger@anywhere.net") == {"admitted": True, "why": "allow-list"}
    assert client.post("/internal/signin-admission", json={"email": "x@y.z"}).status_code == 403


def test_release_admin_hands_the_instance_back_to_first_run(client):
    """The rehearsal needs an instance that has never been claimed, and the account holding the
    role is usually a leftover test identity sitting next to a real one. Role, and only role."""
    uid = _mk_user(client, "release-me@vexa.ai")
    spent = _claim_code(client)
    _claim(client, uid, spent)
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_internal()).json()["is_admin"] is True

    r = client.post("/internal/release-admin", headers=_internal(), json={"user_id": uid})
    assert r.json()["released"] is True and r.json()["admin_exists"] is False
    fresh = r.json()["claim_code"]
    assert fresh and fresh != spent
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_internal()).json()["is_admin"] is False
    # The user itself is untouched — this is not a delete.
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 200
    # Idempotent: releasing a role nobody holds is not an error.
    assert client.post("/internal/release-admin", headers=_internal(),
                       json={"user_id": uid}).json()["released"] is False
    # ...and the next sign-in can claim again — with the newest code; a spent one opens nothing.
    latest = client.post("/internal/release-admin", headers=_internal(),
                         json={"user_id": uid}).json()["claim_code"]
    assert _claim(client, uid, spent).json()["why"] == "bad-code"
    assert _claim(client, uid, latest).json()["claimed"] is True
    assert _claim(client, uid, latest).json()["why"] == "admin-exists"


def test_the_admins_the_deployment_names_are_admins_and_close_the_claim(client, monkeypatch):
    """VEXA_ADMIN_EMAILS is read HERE (the terminal holds no list): those addresses are admins on
    /internal/validate and the role oracle, the instance has an admin, and nobody can claim."""
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "Owner-Test@vexa.ai")
    owner = _mk_user(client, "owner-test@vexa.ai")
    other = _mk_user(client, "other-test@vexa.ai")
    tok = client.post(f"/admin/users/{owner}/tokens?scopes=bot", headers=_admin()).json()["token"]
    assert client.post("/internal/validate", headers=_internal(), json={"token": tok}).json()["is_admin"] is True
    assert client.get(f"/internal/users/{owner}/is-admin", headers=_internal()).json()["is_admin"] is True
    assert client.get(f"/internal/users/{other}/is-admin", headers=_internal()).json()["is_admin"] is False
    assert client.get("/internal/instance", headers=_internal()).json() == {"admin_exists": True}
    assert client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": other}).json() == \
        {"claimed": False, "admin_exists": True, "why": "admin-exists"}
    # no code is handed out while the deployment names the admins
    assert "claim_code" not in client.post("/internal/release-admin", headers=_internal(),
                                           json={"user_id": other}).json()


def test_instance_routes_are_internal_tier(client):
    assert client.get("/internal/instance").status_code == 403
    assert client.post("/internal/release-admin", json={"user_id": 1}).status_code == 403


def test_a_settings_write_that_recognises_nothing_is_refused(client):
    """The 2026-09-02 live blocker, as a test.

    The first-run wizard sent {"global": "handoff"} to record that the admin had left the wizard for
    the setup chat. "global" was not a field of "setup", so the write stored NOTHING and answered
    200. The client could not tell. On the next load the marker was absent, the wizard decided it
    was still mid-wizard, rendered its full-screen overlay INSTEAD of the workbench — so the chat it
    had just handed off to could never mount — and the admin was returned to the same step. From the
    outside the button "did nothing"; underneath, every layer reported success.

    A partially-recognised write still succeeds: a client sending a known field alongside noise is
    not the failure this catches."""
    # the field that was missing, and the reason it is here
    r = client.put("/internal/settings/setup", headers=_internal(), json={"global": "handoff"})
    assert r.status_code == 200 and r.json()["value"]["global"] == "handoff"

    # a write nothing understood is loud, and says what the vocabulary is
    r = client.put("/internal/settings/setup", headers=_internal(), json={"nonsense": "x"})
    assert r.status_code == 400
    assert "nonsense" in r.json()["detail"] and "global" in r.json()["detail"]

    # ...but a recognised field carried alongside an unknown one still lands
    r = client.put("/internal/settings/setup", headers=_internal(),
                   json={"completed": "true", "nonsense": "x"})
    assert r.status_code == 200 and r.json()["value"]["completed"] == "true"
    assert "nonsense" not in r.json()["value"]

    # an empty body is not an error — it is a no-op nobody asked anything of
    assert client.put("/internal/settings/setup", headers=_internal(), json={}).status_code == 200
