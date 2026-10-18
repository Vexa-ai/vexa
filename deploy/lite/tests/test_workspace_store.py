"""agent-api's workspace dir and the runtime's mount target are one path in Lite.

agent-api hands the runtime mount paths under VEXA_WORKSPACES_DIR; the runtime refuses any mount
outside its VEXA_WORKSPACE_MOUNT_TARGET. supervisord sets them separately, and the entrypoint creates
the directory, so all three are held equal here.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITE = ROOT / "deploy" / "lite"


def _program_env(name: str) -> dict[str, str]:
    text = (LITE / "supervisord.conf").read_text()
    body = text.split(f"[program:{name}]")[1].split("\n[")[0]
    line = re.search(r"^environment=(.*)$", body, flags=re.M).group(1)
    return dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"', line))


def test_agent_api_and_the_runtime_name_one_workspace_path():
    path = _program_env("agent-api")["VEXA_WORKSPACES_DIR"]
    assert path == _program_env("runtime")["VEXA_WORKSPACE_MOUNT_TARGET"]
    assert re.search(rf"^mkdir -p {re.escape(path)}\b", (LITE / "entrypoint.sh").read_text(), flags=re.M)
