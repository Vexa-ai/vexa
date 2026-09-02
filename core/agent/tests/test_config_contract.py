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


def _env_names(field_name, field):
    """Every env var this Settings field actually reads — the VEXA_ prefix by default, or the
    explicit `validation_alias` choices where a field carries one (F95: `internal_api_secret` reads
    the canonical `INTERNAL_API_SECRET` first and the prefixed spelling as a deprecated fallback)."""
    alias = getattr(field, "validation_alias", None)
    choices = getattr(alias, "choices", None)
    if choices:
        return [str(c) for c in choices]
    if isinstance(alias, str):
        return [alias]
    return [f"VEXA_{field_name.upper()}"]


def test_every_settings_field_is_declared():
    """pydantic-settings reads env by field name (VEXA_ prefix) — invisible to the gate's literal
    os.getenv scanner, so THIS test holds the sync: a new Settings field must land in the
    declaration (the SSOT) to pass. A field carrying an explicit alias must have EVERY spelling it
    accepts declared, or one name of a secret drifts out of the contract while the other stays in —
    which is precisely the shape F95 arrived in."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    for field_name, field in Settings.model_fields.items():
        for env_name in _env_names(field_name, field):
            assert env_name in declared, (
                f"Settings.{field_name} reads {env_name} but config.v1.json does not declare it — "
                "add it to core/agent/control_plane/config.v1.json"
            )


def test_internal_secret_has_one_canonical_name_and_a_deprecated_alias(monkeypatch):
    """F95 — one secret had three names, and each name grew its own refusal list.

    The canonical name is the compose/helm secret KEY, `INTERNAL_API_SECRET`, the same name
    admin-api, gateway and meeting-api read. The prefixed spelling still resolves so an operator
    mid-upgrade is warned rather than silently dropped into an unauthenticated internal tier — but
    it must never WIN over the canonical one, or a stale export quietly shadows the real value."""
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "deprecated")
    assert load_settings().internal_api_secret.get_secret_value() == "deprecated"

    monkeypatch.setenv("INTERNAL_API_SECRET", "canonical")
    assert load_settings().internal_api_secret.get_secret_value() == "canonical"

    monkeypatch.delenv("VEXA_INTERNAL_API_SECRET", raising=False)
    assert load_settings().internal_api_secret.get_secret_value() == "canonical"

    # Constructing by FIELD name still works — every other test in this tree builds Settings that
    # way, and an alias that broke it would have been a silent test-only regression.
    assert load_settings(internal_api_secret="explicit").internal_api_secret\
        .get_secret_value() == "explicit"


def test_preflight_refuses_a_secretless_or_placeheld_internal_tier():
    """agent-api both PRESENTS the internal secret and BELIEVES it — `_internal_caller` compares
    this value, and the meeting room's gate 0 is by that code's own statement the trust boundary on
    who is in the room. Unset used to mean `_internal_caller` simply returned False, which is a
    half-configured tier nobody can see; a PUBLISHED placeholder is worse, because it is that tier
    handed to every reader of the repository (F95)."""
    with pytest.raises(cp.ConfigError) as ei:
        cp.preflight({})
    assert "INTERNAL_API_SECRET" in str(ei.value)
    for placeholder in ("vexa-internal-secret", "lite-internal-secret", "changeme"):
        with pytest.raises(cp.ConfigError) as ei:
            cp.preflight({"INTERNAL_API_SECRET": placeholder})
        assert "INTERNAL_API_SECRET" in str(ei.value)
        assert placeholder not in str(ei.value), "a refusal must never echo the value"
    cp.preflight({"INTERNAL_API_SECRET": "a-real-secret"})


def test_capability_tri_states():
    assert cp.capability_states({})["bot_gateway"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"VEXA_BOT_API_KEY": "k"})["bot_gateway"] == cp.CONFIGURED
    # mode=any: any ONE model-credential path configures the agent plane's model_inference row
    assert cp.capability_states({})["model_inference"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"HOST_CLAUDE_CREDENTIALS": "/x.json"})["model_inference"] == cp.CONFIGURED
    assert cp.capability_states({"ANTHROPIC_AUTH_TOKEN": "tok"})["model_inference"] == cp.CONFIGURED


def test_preflight_reports_capability_rows(monkeypatch):
    """agent-api used to have NO required-explicit key, and this test was named for that fact. F95
    gave it one — INTERNAL_API_SECRET, which it both presents and believes — so the environment now
    has to carry it before the capability rows can be reached at all."""
    for k in ("VEXA_BOT_API_KEY", "HOST_CLAUDE_CREDENTIALS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INTERNAL_API_SECRET", "a-real-secret")
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
