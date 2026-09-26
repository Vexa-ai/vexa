"""Fixture collection (O-TEL-1) — the ``capture_signal`` flag on ``/internal/users/{id}/bot-context``.

A tape is raw meeting audio kept beside the user's recording, so identity never invents a default
for it. It carries a DECISION when a user or platform setting states one, resolved
user > platform_settings, in either direction; with no decision the key is absent and the spawn
falls back to the deployment's own default (meeting-api ``CAPTURE_SIGNAL_ENABLED``, off unless set).

Two layers, deliberately split:
  * the RESOLVER (``_resolve_capture_signal``) — pure, so the two tiers + the no-decision case + the
    unrecognized-value fall-through are provable with no docker, no DB, no HTTP;
  * the EDGE (bot-context over the internal secret) — the same testcontainers-PG harness the other
    settings evals use, proving a platform decision reaches the response in both directions and that
    clearing it returns to "no decision" rather than to a stored value.

The per-user tier has no HTTP writer today (``UserAdminPatch.data`` is a closed billing model), so
it is a DB-level escape hatch — covered at the resolver, which is where its logic lives.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import _resolve_capture_signal, create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine


# ── the resolver: pure, always runs (no docker) ────────────────────────────────────────────────

def test_capture_signal_is_no_decision_when_nothing_is_configured():
    # Nothing configured = no decision, never an implicit ON: a deployment nobody configured
    # tapes nothing unless its operator turned CAPTURE_SIGNAL_ENABLED on.
    assert _resolve_capture_signal({}, {}) is None
    assert _resolve_capture_signal({"diagnostics": {}}, {}) is None
    assert _resolve_capture_signal({}, {"capture_signal": ""}) is None


def test_capture_signal_platform_setting_decides_both_ways():
    # One settings write switches the whole deployment, no redeploy — on or off.
    assert _resolve_capture_signal({}, {"capture_signal": "false"}) is False
    assert _resolve_capture_signal({}, {"capture_signal": "0"}) is False
    assert _resolve_capture_signal({}, {"capture_signal": "off"}) is False
    assert _resolve_capture_signal({}, {"capture_signal": "true"}) is True


def test_capture_signal_user_beats_platform_in_both_directions():
    off_user = {"diagnostics": {"capture_signal": "false"}}
    on_user = {"diagnostics": {"capture_signal": "true"}}
    # An account that must not be taped stays off even where the platform collects…
    assert _resolve_capture_signal(off_user, {"capture_signal": "true"}) is False
    assert _resolve_capture_signal(off_user, {}) is False
    # …and an explicit per-user ON tapes one debug account while the platform stays off.
    assert _resolve_capture_signal(on_user, {"capture_signal": "false"}) is True
    assert _resolve_capture_signal(on_user, {}) is True
    # Booleans read the same as the string form (a DB-level write may store a real JSON bool).
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": False}}, {}) is False


def test_capture_signal_unrecognized_value_falls_through_instead_of_guessing():
    # A typo is not a decision (meeting-api env_flag's rule). It falls through to the next tier —
    # so a mistyped USER value still sees the platform setting, and with none it stays no decision.
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": "flase"}},
                                   {"capture_signal": "false"}) is False
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": "flase"}}, {}) is None
    # A non-dict diagnostics blob never raises — it is no decision.
    assert _resolve_capture_signal({"diagnostics": "nope"}, {}) is None


# ── the edge: bot-context over the internal secret (testcontainers PG) ─────────────────────────

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


@requires_docker
def test_bot_context_carries_a_capture_signal_decision_only_when_one_is_set(client):
    uid = client.post("/admin/users", headers=_admin(),
                      json={"email": "capture@vexa.ai"}).json()["id"]

    # No setting anywhere: the key is absent, so the spawn applies the deployment default.
    body = client.get(f"/internal/users/{uid}/bot-context", headers=_internal()).json()
    assert "capture_signal" not in body

    # The platform enable: one settings write, every subsequent spawn tapes.
    r = client.put("/internal/settings/diagnostics", headers=_internal(),
                   json={"capture_signal": "true"})
    assert r.status_code == 200, r.text
    assert client.get(f"/internal/users/{uid}/bot-context",
                      headers=_internal()).json()["capture_signal"] is True

    # The platform stop travels as an explicit false (it must override an enabled deployment).
    client.put("/internal/settings/diagnostics", headers=_internal(), json={"capture_signal": "false"})
    assert client.get(f"/internal/users/{uid}/bot-context",
                      headers=_internal()).json()["capture_signal"] is False

    # Clearing the field (the settings writers' "" = clear semantics) returns to no decision.
    client.put("/internal/settings/diagnostics", headers=_internal(), json={"capture_signal": ""})
    assert client.get("/internal/settings/diagnostics", headers=_internal()).json()["value"] == {}
    assert "capture_signal" not in client.get(f"/internal/users/{uid}/bot-context",
                                              headers=_internal()).json()
