"""The workspace store has one path, on every side of compose (offline — no stack, no docker).

agent-api keeps workspaces under ``VEXA_WORKSPACES_DIR`` on the ``agent-workspaces`` volume and hands
the runtime mount paths under it; the runtime refuses any mount outside its own
``VEXA_WORKSPACE_MOUNT_TARGET``. The three are written separately in docker-compose.yml, so they are
held equal here: a difference refuses every dispatch. The runtime's one out-of-store mount source is
agent-api's ``_global`` tier when an operator keeps it outside the store.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"


def _service(name: str) -> str:
    """One service block, as text — the same line-wise read `gate:config-contract` uses."""
    lines = COMPOSE.read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.rstrip() == f"  {name}:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i][:3].strip() and not lines[i].startswith("   ") and lines[i].strip():
            end = i
            break
    return "\n".join(lines[start:end])


def _env(block: str, key: str) -> str:
    (value,) = re.findall(rf"^\s+- {key}=(.*)$", block, flags=re.M)
    return value.strip()


def test_agent_api_and_the_runtime_name_one_workspace_path():
    agent_api, runtime = _service("agent-api"), _service("runtime")
    workspaces_dir = _env(agent_api, "VEXA_WORKSPACES_DIR")
    (store_mount,) = re.findall(r"^\s+- agent-workspaces:(\S+)$", agent_api, flags=re.M)
    assert workspaces_dir == store_mount == _env(runtime, "VEXA_WORKSPACE_MOUNT_TARGET")


def test_the_runtimes_out_of_store_source_is_the_global_tier_only():
    runtime = _service("runtime")
    assert _env(runtime, "RUNTIME_EXTRA_MOUNT_SOURCES") == "${VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH:-}"
