"""Fixture collection (O-TEL-1) — the ``capture_signal`` flag on ``/internal/users/{id}/bot-context``.

Prod meetings are the fixture source, so capture is **default ON**: a deployment that has never
heard of the flag tapes every meeting. The flag is a KILL switch, resolved user > platform_settings
> default-on, and its whole reason to exist is stopping collection fleet-wide with no redeploy.

Two layers, deliberately split:
  * the RESOLVER (``_resolve_capture_signal``) — pure, so the three resolutions + the default + the
    unrecognized-value fall-through are provable with no docker, no DB, no HTTP;
  * the EDGE (bot-context over the internal secret) — the same testcontainers-PG harness the other
    settings evals use, proving the platform kill switch reaches the response and that clearing it
    restores the default.

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

def test_capture_signal_defaults_on_when_nothing_is_configured():
    # Absence of every flag = ON. This is the founder ruling made executable: a deployment nobody
    # has configured still produces fixtures.
    assert _resolve_capture_signal({}, {}) is True
    assert _resolve_capture_signal({"diagnostics": {}}, {}) is True
    assert _resolve_capture_signal({}, {"capture_signal": ""}) is True


def test_capture_signal_platform_kill_switch():
    # The one operation that matters in an incident: one settings write, no redeploy, all bots stop.
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
    # …and an explicit per-user ON survives the platform kill switch (opt-in for a debug account).
    assert _resolve_capture_signal(on_user, {"capture_signal": "false"}) is True
    # Booleans read the same as the string form (a DB-level write may store a real JSON bool).
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": False}}, {}) is False


def test_capture_signal_unrecognized_value_falls_through_instead_of_guessing():
    # A typo is not an explicit opt-out (meeting-api env_flag's rule). It falls through to the next
    # tier — so a mistyped USER value still sees the platform kill switch, rather than silently
    # re-enabling collection on an account the operator meant to exclude.
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": "flase"}},
                                   {"capture_signal": "false"}) is False
    assert _resolve_capture_signal({"diagnostics": {"capture_signal": "flase"}}, {}) is True
    # A non-dict diagnostics blob never raises — it resolves to the default.
    assert _resolve_capture_signal({"diagnostics": "nope"}, {}) is True


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
def test_bot_context_carries_capture_signal_and_honors_the_kill_switch(client):
    uid = client.post("/admin/users", headers=_admin(),
                      json={"email": "capture@vexa.ai"}).json()["id"]

    # Default ON, and ALWAYS present — bot_spawn must not have to distinguish "absent" from
    # "identity unreachable", so the key is stated rather than omitted (unlike `transcription`).
    body = client.get(f"/internal/users/{uid}/bot-context", headers=_internal()).json()
    assert body["capture_signal"] is True

    # The kill switch: one settings write, every subsequent spawn stops taping.
    r = client.put("/internal/settings/diagnostics", headers=_internal(),
                   json={"capture_signal": "false"})
    assert r.status_code == 200, r.text
    assert client.get(f"/internal/users/{uid}/bot-context",
                      headers=_internal()).json()["capture_signal"] is False

    # …and it is reversible by clearing the field (the settings writers' "" = clear semantics),
    # which returns to the default rather than to a stored "true".
    client.put("/internal/settings/diagnostics", headers=_internal(), json={"capture_signal": ""})
    assert client.get("/internal/settings/diagnostics", headers=_internal()).json()["value"] == {}
    assert client.get(f"/internal/users/{uid}/bot-context",
                      headers=_internal()).json()["capture_signal"] is True
