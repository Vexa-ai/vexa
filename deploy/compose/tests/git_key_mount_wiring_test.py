"""agent-api holds the broker's git role key only while the Git store is switched on (offline — no
stack, no docker).

The key lets its holder read every person's saved Git token and the workspaces' deploy keys through
the broker. With ``VEXA_GIT_STORE_BROKER_URL`` unset agent-api keeps those in its own store and never
uses the key, so it must not be mounted: the mount point is then an empty volume.
"""
from __future__ import annotations

import re

from workspace_store_wiring_test import COMPOSE, _service

_VAR = re.compile(r"\$\{([A-Z0-9_]+)(:?[-+])([^}]*)\}")


def _interpolate(text: str, env: dict) -> str:
    """Compose's ``${VAR:-default}``, ``${VAR-default}`` and ``${VAR:+replacement}`` forms."""
    def one(m: re.Match) -> str:
        name, op, arg = m.groups()
        value = env.get(name)
        if op == ":+":
            return arg if value else ""
        if op == "+":
            return arg if value is not None else ""
        if op == ":-":
            return value if value else arg
        return value if value is not None else arg
    return _VAR.sub(one, text)


def _agent_api_mounts(env: dict) -> dict:
    block = _interpolate(_service("agent-api"), env)
    mounts = re.findall(r"^\s+- ([A-Za-z0-9_.-]+):(/run/vexa-connections/\S+?)(?::ro)?$", block, flags=re.M)
    return {target: source for source, target in mounts}


def test_the_git_key_is_not_mounted_while_the_git_store_is_off():
    mounts = _agent_api_mounts({})
    assert mounts["/run/vexa-connections/git"] != "connections-git-key"
    assert mounts["/run/vexa-connections/agent"] == "connections-agent-key"


def test_the_git_key_is_mounted_once_the_git_store_is_on():
    mounts = _agent_api_mounts({"VEXA_GIT_STORE_BROKER_URL": "http://credential-broker:8100"})
    assert mounts["/run/vexa-connections/git"] == "connections-git-key"


def test_the_empty_mount_point_is_a_declared_volume_nobody_writes():
    source = _agent_api_mounts({})["/run/vexa-connections/git"]
    text = COMPOSE.read_text()
    assert re.search(rf"^  {re.escape(source)}:\s*$", text, flags=re.M)
    assert not re.search(rf"^\s+- {re.escape(source)}:/(?!run/vexa-connections/git:ro)", text, flags=re.M)
