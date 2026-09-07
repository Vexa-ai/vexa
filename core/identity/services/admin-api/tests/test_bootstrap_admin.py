"""First-run bootstrap admin — /internal/instance + /internal/bootstrap-admin + the is_admin
surfacing on /internal/validate (the terminal admin gate's input).

Contract (first-run onboarding design, 2026-07-09): a fresh instance has NO admin; the first
sign-in claims the role exactly once (advisory-lock serialized); later sign-ins never claim.
The role lives in users.data["is_admin"] — no schema migration.

Same testcontainers-PG harness as the other suites (skips without docker).
"""
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
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _mk_user(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def test_instance_and_bootstrap_gate_fail_closed(client):
    # internal edge only — no/wrong secret rejected
    assert client.get("/internal/instance").status_code == 403
    assert client.post("/internal/bootstrap-admin",
                       headers={"X-Internal-Secret": "wrong"},
                       json={"user_id": 1}).status_code == 403


def test_first_claim_wins_then_idempotent(client):
    a = _mk_user(client, "first-test@vexa.ai")
    b = _mk_user(client, "second-test@vexa.ai")

    # fresh instance: no admin, and no company layer either
    r = client.get("/internal/instance", headers=_internal())
    assert r.status_code == 200
    assert r.json() == {"admin_exists": False, "global_setup": "missing", "company": None}

    # first sign-in claims
    r = client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": a})
    assert r.status_code == 200 and r.json() == {"claimed": True, "admin_exists": True}

    # instance now has an admin. The company-layer gate is a SEPARATE fact and stays up:
    # claiming the role is not setting the instance up.
    assert client.get("/internal/instance", headers=_internal()).json() == {
        "admin_exists": True, "global_setup": "missing", "company": None}

    # a later user never claims; the admin re-claiming is a harmless no-op
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": b}).json() == {"claimed": False, "admin_exists": True}
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": a}).json() == {"claimed": False, "admin_exists": True}


def test_bootstrap_unknown_user_404(client):
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": 99999}).status_code == 404
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={}).status_code == 404


def test_validate_surfaces_is_admin(client):
    uid = _mk_user(client, "admin-test@vexa.ai")
    tok = client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin()).json()["token"]

    # before the claim: not an admin
    r = client.post("/internal/validate", headers=_internal(), json={"token": tok})
    assert r.status_code == 200 and r.json()["is_admin"] is False

    client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": uid})
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


# ── the company-layer gate ──────────────────────────────────────────────────────────────────────
# The instance state that decides whether this Vexa serves anyone. Read fail-closed by every
# service that can SEND, so the tests below are mostly about what counts as "not completed".

def test_gate_reads_fail_closed_on_anything_but_completed(client):
    """Only the string "completed" opens the gate — surrounding whitespace trimmed, nothing else.

    A typo, a half-written value, a cleared field and an absent row must all read the same way,
    because the alternative is an instance that starts mailing strangers on behalf of a company
    nobody has described — and that failure is not visible from any screen until it has happened.
    Whitespace IS forgiven (a padded value is a copy-paste, not an attempt on the gate); a
    different spelling, including a different case, is not."""
    for value in ("", "missing", "complete", "COMPLETED", "completed ", "true", "yes"):
        client.put("/internal/settings/global_setup", headers=_internal(),
                   json={"state": value})
        expected = "completed" if value.strip() == "completed" else "missing"
        assert client.get("/internal/instance", headers=_internal()).json()["global_setup"] == expected, value
    client.put("/internal/settings/global_setup", headers=_internal(),
               json={"state": "completed", "company": "Acme GmbH"})
    body = client.get("/internal/instance", headers=_internal()).json()
    assert body["global_setup"] == "completed" and body["company"] == "Acme GmbH"


def test_admin_door_returns_the_same_instance_state(client):
    """The flows engine holds an admin key and no internal secret. Two transports, ONE computation
    — a service that has to infer the gate from something else IS a second source of truth."""
    client.put("/internal/settings/global_setup", headers=_internal(),
               json={"state": "completed", "company": "Acme GmbH"})
    assert client.get("/admin/instance", headers=_admin()).json() == \
        client.get("/internal/instance", headers=_internal()).json()
    assert client.get("/admin/instance").status_code in (401, 403)


def test_signin_allowed_while_the_gate_is_up(client):
    admin = _mk_user(client, "gate-admin@vexa.ai")
    other = _mk_user(client, "gate-other@vexa.ai")

    # A VIRGIN instance admits everybody: the next sign-in is the claim, so refusing here would
    # make a fresh install unclaimable — a deadlock, not a gate.
    r = client.post("/internal/signin-allowed", headers=_internal(), json={"email": "anyone@x.io"})
    assert r.json()["allowed"] is True

    client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": admin})

    # Now exactly one person gets in.
    assert client.post("/internal/signin-allowed", headers=_internal(),
                       json={"email": "gate-admin@vexa.ai"}).json()["allowed"] is True
    assert client.post("/internal/signin-allowed", headers=_internal(),
                       json={"email": "GATE-ADMIN@VEXA.AI"}).json()["allowed"] is True
    refused = client.post("/internal/signin-allowed", headers=_internal(),
                          json={"email": "gate-other@vexa.ai"}).json()
    assert refused["allowed"] is False
    assert refused["reason"] == "This Vexa is being set up by its administrator."
    # An address with no account at all is refused the same way — and note that ASKING must never
    # create one, which is why this endpoint only ever reads.
    assert client.post("/internal/signin-allowed", headers=_internal(),
                       json={"email": "stranger@nowhere.io"}).json()["allowed"] is False
    assert client.get(f"/admin/users/{other}", headers=_admin()).status_code == 200

    # Once the layer is accepted the gate is a formality.
    client.put("/internal/settings/global_setup", headers=_internal(), json={"state": "completed"})
    assert client.post("/internal/signin-allowed", headers=_internal(),
                       json={"email": "gate-other@vexa.ai"}).json()["allowed"] is True


def test_release_admin_hands_the_instance_back_to_first_run(client):
    """The rehearsal needs an instance that has never been claimed, and the account holding the
    role is usually a leftover test identity sitting next to a real one. Role, and only role."""
    uid = _mk_user(client, "release-me@vexa.ai")
    client.post("/internal/bootstrap-admin", headers=_internal(), json={"user_id": uid})
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_internal()).json()["is_admin"] is True

    r = client.post("/internal/release-admin", headers=_internal(), json={"user_id": uid})
    assert r.json()["released"] is True and r.json()["admin_exists"] is False
    assert client.get(f"/internal/users/{uid}/is-admin", headers=_internal()).json()["is_admin"] is False
    # The user itself is untouched — this is not a delete.
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 200
    # Idempotent: releasing a role nobody holds is not an error.
    assert client.post("/internal/release-admin", headers=_internal(),
                       json={"user_id": uid}).json()["released"] is False
    # ...and the next sign-in can claim again.
    assert client.post("/internal/bootstrap-admin", headers=_internal(),
                       json={"user_id": uid}).json()["claimed"] is True


def test_gate_routes_are_internal_tier(client):
    assert client.get("/internal/instance").status_code == 403
    assert client.post("/internal/signin-allowed", json={"email": "x@y.z"}).status_code == 403
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
