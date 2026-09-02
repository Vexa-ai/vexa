"""config.v1 (ADR-0026) — agent-api's declaration, the pydantic-settings ↔ declaration sync (every
``Settings`` field's VEXA_* env name must be declared — the Python-side half of what
gate:config-contract's regex scanner cannot introspect), the capability tri-states (bot_gateway ·
model_inference), and the ADDITIVE /health rows next to the existing dispatcher check.
"""
from __future__ import annotations

import re
from pathlib import Path

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


# ── the WORKER and the HARNESS are part of this service's config surface (F91 · F93) ─────────────

_ENV_READ = re.compile(r"""os\.(?:getenv|environ\.get|environ\.setdefault)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
                       r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]""")

#: Process plumbing a module may read without a declaration — the same tight list gate:config-contract
#: keeps (CONFIG_SURFACE_ALLOW). Interpreter/runtime wiring only, never product config.
_PLUMBING = {"PYTHONUNBUFFERED", "PYTHONPATH", "DISPLAY", "NODE_ENV", "HOSTNAME", "TZ", "PGTZ",
             "HOME", "TMPDIR"}


def _env_reads(*dirs: str) -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    found: dict[str, str] = {}
    for d in dirs:
        for path in sorted((root / d).rglob("*.py")):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            for m in _ENV_READ.finditer(path.read_text(encoding="utf-8")):
                found.setdefault(m.group(1) or m.group(2), str(path.relative_to(root)))
    return found


def test_every_env_read_in_the_harness_and_the_worker_is_declared():
    """F91. gate:config-contract scanned only `control_plane` + `shared`, so every dial the TURN
    actually runs on was invisible to the contract — `VEXA_LLM_EXTRA_BODY`, whose absence fails
    silently (the turn runs with thinking on), was declared nowhere at all. agent-api does not read
    these itself; it STAMPS them into every worker spec env, which is exactly why they are its
    config surface. This is the Python-side twin of the widened gate scan: either alone can be
    edited away, both cannot be by accident."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    for key, where in sorted(_env_reads("llm", "worker").items()):
        assert key in declared or key in _PLUMBING, (
            f"{where} reads {key} but config.v1.json does not declare it — add it to "
            "core/agent/control_plane/config.v1.json"
        )


def test_the_qwen_lane_dials_are_declared():
    """Named one by one because these are the keys whose absence is SILENT: a mis-stamped
    extra_body leaves thinking on and the turn merely returns nothing parseable."""
    declared = {k["key"] for k in cp.load_declaration()["keys"]}
    assert {"VEXA_LLM_BASE_URL", "VEXA_LLM_API_KEY", "VEXA_LLM_MODEL", "VEXA_LLM_EXTRA_BODY",
            "VEXA_AGENT_MODEL", "VEXA_AGENT_STREAM", "VEXA_AGENT_MAX_TOOL_CALLS",
            "VEXA_AGENT_MAX_TURN_SEC", "VEXA_AGENT_CONTEXT_TOKENS", "VEXA_MOUNTS",
            "VEXA_RUNNER"} <= declared


#: The number gate:config-contract PRINTS. It used to be compared to nothing, so it could move by
#: any amount — a key silently dropped from the declaration reads as a smaller, equally green line
#: (F93). Bump this deliberately, in the same commit that adds or removes a key.
EXPECTED_DECLARED_KEYS = 84


def test_the_declared_key_count_is_asserted_not_merely_printed():
    decl = cp.load_declaration()
    assert len(decl["keys"]) == EXPECTED_DECLARED_KEYS, (
        f"agent-api declares {len(decl['keys'])} keys, this tripwire expects "
        f"{EXPECTED_DECLARED_KEYS}. If the change is intended, update EXPECTED_DECLARED_KEYS here "
        "in the same commit — the gate prints this number and comparing it to nothing is how a "
        "dropped declaration stays green."
    )
    assert len({k["key"] for k in decl["keys"]}) == len(decl["keys"]), "a key is declared twice"
