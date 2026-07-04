"""config.v1 (ADR-0026) — agent-api's declaration, the pydantic-settings ↔ declaration sync (every
``Settings`` field's VEXA_* env name must be declared — the Python-side half of what
gate:config-contract's regex scanner cannot introspect), the capability tri-states (bot_gateway ·
model_inference), and the ADDITIVE /health rows next to the existing dispatcher check.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane import config_preflight as cp
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import Settings, load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "fake-token"


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    cp._reset_probe_cache()
    yield
    cp._reset_probe_cache()


def test_declaration_loads_and_is_internally_consistent():
    decl = cp.load_declaration()
    assert decl["service"] == "agent-api"
    assert set(decl["capabilities"]) == {"bot_gateway", "model_inference"}
    assert decl["capabilities"]["model_inference"]["mode"] == "any"


def test_every_settings_field_is_declared():
    """pydantic-settings reads env by field name (VEXA_ prefix) — invisible to the gate's literal
    os.getenv scanner, so THIS test holds the sync: a new Settings field must land in the
    declaration (the SSOT) to pass."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    for field in Settings.model_fields:
        env_name = f"VEXA_{field.upper()}"
        assert env_name in declared, (
            f"Settings.{field} reads {env_name} but config.v1.json does not declare it — "
            "add it to core/agent/control_plane/config.v1.json"
        )


def test_capability_tri_states():
    assert cp.capability_states({})["bot_gateway"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"VEXA_BOT_API_KEY": "k"})["bot_gateway"] == cp.CONFIGURED
    # mode=any: any ONE model-credential path configures the agent plane's model_inference row
    assert cp.capability_states({})["model_inference"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"HOST_CLAUDE_CREDENTIALS": "/x.json"})["model_inference"] == cp.CONFIGURED
    assert cp.capability_states({"ANTHROPIC_AUTH_TOKEN": "tok"})["model_inference"] == cp.CONFIGURED


def test_preflight_has_no_required_keys_and_reports_rows(monkeypatch):
    for k in ("VEXA_BOT_API_KEY", "HOST_CLAUDE_CREDENTIALS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    report = cp.preflight()
    assert report["service"] == "agent-api"
    assert report["capabilities"]["bot_gateway"]["state"] == cp.NOT_CONFIGURED
    assert report["capabilities"]["model_inference"]["state"] == cp.NOT_CONFIGURED


def test_health_carries_capability_rows_additively(monkeypatch):
    for k in ("VEXA_BOT_API_KEY", "HOST_CLAUDE_CREDENTIALS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()))
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    # the pre-existing consumers' keys are untouched
    assert body["status"] == "ok"
    assert body["service"] == "agent-api"
    assert body["checks"]["dispatcher"] is True
    # the additive config.v1 rows; unconfigured capabilities NEVER degrade status
    assert body["capabilities"]["bot_gateway"]["state"] == cp.NOT_CONFIGURED
    assert body["capabilities"]["model_inference"]["state"] == cp.NOT_CONFIGURED


def test_health_degraded_path_still_carries_rows():
    # the dispatcher-absent 503 (P18) keeps its shape AND gains the rows
    r = TestClient(create_app(None)).get("/health")  # type: ignore[arg-type]
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "capabilities" in body
