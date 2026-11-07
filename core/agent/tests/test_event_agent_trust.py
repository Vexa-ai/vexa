"""An event agent is autonomous and untrusted (ADR-0046 decision 12): it reads and proposes. Its
turn mounts every workspace read-only, its tools may write no workspace, and a draft, a send, a
share or a connection write is refused — the act becomes a proposal the person presses."""
from __future__ import annotations

import json

from control_plane import connection_policy
from control_plane.dispatch import build_unit_env
from shared import delegation
from shared.config import load_settings

INV = {"identity": {"subject": "u_1", "launcher": "user:u_1"}, "runner": "openai-agent",
       "workspaces": [{"id": "u_1", "mode": "rw"}], "trigger": "scheduled",
       "context": {"kind": "generic", "session": "concern-abc"},
       "start": {"entrypoint": {"inline": "follow up"}}}


def _env(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("VEXA_MCP_DELEGATION_SECRET", "test-delegation-secret-0123456789abcdef")
    monkeypatch.setenv("VEXA_MCP_URL", "http://mcp.test/mcp")
    return build_unit_env(load_settings(workspaces_dir=str(tmp_path)), INV, unit_id="u", token="t", **kw)


def test_an_event_agent_mounts_nothing_writable_and_its_tools_write_no_workspace(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch, least_privilege=True)
    assert env["VEXA_LEAST_PRIVILEGE"] == "event_agent"
    assert all(m.get("write") is False for m in json.loads(env["VEXA_MOUNTS"]))
    token = env.get("VEXA_MCP_DELEGATION_TOKEN")
    if token:
        claims = delegation.verify_delegation("test-delegation-secret-0123456789abcdef", token)
        assert list((claims.get("scope") or claims.get("delegation") or {}).get("workspaces") or []) == []


def test_a_routine_turn_keeps_its_writable_desk(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    assert "VEXA_LEAST_PRIVILEGE" not in env
    assert any(m.get("write") for m in json.loads(env["VEXA_MOUNTS"]))


def test_an_unattended_draft_send_or_calendar_change_is_refused_by_policy(tmp_path):
    eff = connection_policy.read(tmp_path)          # no POLICIES.md: the defaults
    for action in ("gmail.draft", "gmail.draft.update", "calendar.event.create"):
        assert connection_policy.allowed(eff, action, unattended=True) is not None, action
