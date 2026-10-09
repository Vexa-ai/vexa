"""What a dispatch stamps into a worker's spec env — and what it leaves to the runtime.

The runtime decides where the workspace store comes from and drops any ``VEXA_WORKSPACE_MOUNT_*``
key a spec carries (``core/runtime/src/runtime_kernel/workload_env.py``), so agent-api neither reads
nor stamps the store backing.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from control_plane import config_preflight as cp
from control_plane.dispatch import build_unit_env
from shared.config import Settings, load_settings

REPO = Path(__file__).resolve().parents[3]

INV = {"identity": {"subject": "u_1", "launcher": "user:u_1"}, "runner": "openai-agent",
       "workspaces": [{"id": "u_1", "mode": "rw"}], "trigger": "message",
       "start": {"entrypoint": {"inline": "hi"}}}


def _runtime_owned_prefixes() -> tuple:
    """The runtime's own list, read from its module (stdlib-only) rather than copied here."""
    path = REPO / "core" / "runtime" / "src" / "runtime_kernel" / "workload_env.py"
    name = "runtime_kernel_workload_env_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module      # @dataclass resolves its own module through sys.modules
    spec.loader.exec_module(module)
    return module.RUNTIME_OWNED_PREFIXES


def _env(tmp_path, **settings) -> dict:
    return build_unit_env(load_settings(workspaces_dir=str(tmp_path), **settings), INV,
                          unit_id="unit-1", token="tok")


# ── the workspace store backing is the runtime's ─────────────────────────────────────────────────

def test_the_dispatch_stamps_no_key_the_runtime_owns(tmp_path):
    prefixes = _runtime_owned_prefixes()
    assert "VEXA_WORKSPACE_MOUNT_" in prefixes
    env = _env(tmp_path)
    assert [k for k in env if k.startswith(prefixes)] == []
    # what the worker does need still travels: its mount set and its cwd, both under the store root
    assert json.loads(env["VEXA_MOUNTS"]) and env["VEXA_WORKSPACE_PATH"].startswith(str(tmp_path))


def test_agent_api_neither_reads_nor_declares_the_store_backing():
    assert "workspace_mount_source" not in Settings.model_fields
    decl = cp.load_declaration()
    assert "VEXA_WORKSPACE_MOUNT_SOURCE" not in {k["key"] for k in decl["keys"]}
    # a surface that still sets it is documented drift, not a key this service reads
    surface_only = {k["key"]: k["reason"] for k in decl.get("surface_only") or []}
    assert "runtime" in surface_only["VEXA_WORKSPACE_MOUNT_SOURCE"]


# ── the configured workspace template reaches the worker ─────────────────────────────────────────

def test_the_dispatch_stamps_the_configured_template(tmp_path):
    assert _env(tmp_path, default_template="acme")["VEXA_DEFAULT_TEMPLATE"] == "acme"
    assert _env(tmp_path)["VEXA_DEFAULT_TEMPLATE"] == "default"


def test_the_worker_loads_the_stamped_templates_skills(tmp_path, monkeypatch):
    """The worker resolves its platform skills — and any workspace it seeds itself — from the
    template agent-api stamped, against its OWN seeds root."""
    from llm.claude_skills import _governed_skills_dir
    from shared.seeding import resolve_seed_dir

    seeds = tmp_path / "seeds"
    for name in ("default", "acme"):
        (seeds / name / "skills" / "brief").mkdir(parents=True)
        (seeds / name / "skills" / "brief" / "SKILL.md").write_text(name)
    monkeypatch.delenv("VEXA_WORKSPACE_SEED_DIR", raising=False)
    monkeypatch.setenv("VEXA_WORKSPACE_SEEDS_DIR", str(seeds))
    monkeypatch.delenv("VEXA_DEFAULT_TEMPLATE", raising=False)
    assert _governed_skills_dir() == seeds / "default" / "skills"
    monkeypatch.setenv("VEXA_DEFAULT_TEMPLATE", "acme")
    assert _governed_skills_dir() == seeds / "acme" / "skills"
    assert resolve_seed_dir() == seeds / "acme"
    # an explicit template still wins over the environment, and the explicit seed dir over both
    assert resolve_seed_dir("other") == seeds / "other"
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(tmp_path / "pinned"))
    assert resolve_seed_dir("other") == tmp_path / "pinned"


def test_the_declared_template_default_is_the_code_default():
    from shared.seeding import DEFAULT_TEMPLATE

    row = next(k for k in cp.load_declaration()["keys"] if k["key"] == "VEXA_DEFAULT_TEMPLATE")
    assert row["default"] == Settings.model_fields["default_template"].default == DEFAULT_TEMPLATE
