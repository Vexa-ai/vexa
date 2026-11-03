"""agent-api holds the broker's git role key, and keeps no Git credential of its own (offline — no
stack, no docker).

Saved Git tokens and deploy keys live in the credential broker (ADR-0040); agent-api reaches them as
the `git` role, so the key is mounted by default and the URL points at the broker compose deploys.
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


def test_the_git_key_and_the_broker_are_wired_by_default():
    mounts = _agent_api_mounts({})
    assert mounts["/run/vexa-connections/git"] == "connections-git-key"
    assert mounts["/run/vexa-connections/agent"] == "connections-agent-key"
    block = _interpolate(_service("agent-api"), {})
    assert "VEXA_GIT_STORE_BROKER_URL=http://credential-broker:8100" in block
    assert "VEXA_GIT_STORE_KEY_FILE=/run/vexa-connections/git/key" in block


def test_agent_api_never_mounts_the_human_or_store_key():
    mounts = _agent_api_mounts({})
    assert set(mounts) == {"/run/vexa-connections/agent", "/run/vexa-connections/git"}
