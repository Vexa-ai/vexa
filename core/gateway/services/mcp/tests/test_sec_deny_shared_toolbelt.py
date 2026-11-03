"""Deny test from the private 2026-10-17 security review (C1, question 1): the MCP edge shows a
shared (room) delegation no person-wide tool. D3 (0.13.4) closes it: a shared caller is shown only
the tools whose domain manifest declares `shared: allow`, and nothing at all when no table is known."""
from __future__ import annotations

import pytest

from vexa_mcp.visibility import Policy, is_shared_caller

PERSON_WIDE = ["people_followups", "people_discover", "customer_profile", "notifications_list",
               "home_history", "chat_read"]

ROOM_CALLER = {"x-user-id": "alice", "x-user-regime": "human", "x-user-delegation-shared": "1",
               "x-user-delegation-workspaces": "team"}
WS_PRINCIPAL = {"x-user-id": "alice", "x-user-regime": "human",
                "x-user-principal-kind": "workspace", "x-user-principal-workspace": "team",
                "x-user-delegation-workspaces": "team"}
PERSON = {"x-user-id": "alice"}


@pytest.mark.parametrize("tool", PERSON_WIDE)
def test_shared_delegation_is_shown_no_person_wide_tool(tool):
    policy = Policy.from_env({})
    assert not policy.shown(tool, ROOM_CALLER), tool


@pytest.mark.parametrize("tool", PERSON_WIDE)
def test_a_declared_table_shows_a_shared_caller_only_its_allowed_tools(tool):
    policy = Policy.from_env({}, shared_source=lambda: ["workspace_read", "workspace_write"])
    assert not policy.shown(tool, ROOM_CALLER)
    assert not policy.shown(tool, WS_PRINCIPAL)
    assert policy.shown(tool, PERSON), "a person's own caller is not narrowed by D3"
    assert policy.shown("workspace_read", ROOM_CALLER)
    assert policy.shown("workspace_read", WS_PRINCIPAL)


def test_a_workspace_principal_is_shared_even_without_the_shared_header():
    assert is_shared_caller(WS_PRINCIPAL)
    assert is_shared_caller(ROOM_CALLER)
    assert not is_shared_caller(PERSON)


def test_the_cohort_does_not_widen_a_shared_caller():
    policy = Policy.from_env({"VEXA_MCP_TOOLS_COHORT": "*"}, shared_source=lambda: ["workspace_read"])
    assert not policy.shown("people_followups", ROOM_CALLER)


def test_an_unreadable_table_shows_a_shared_caller_nothing():
    def boom():
        raise RuntimeError("assembly not ready")
    policy = Policy.from_env({}, shared_source=boom)
    assert not policy.shown("workspace_read", ROOM_CALLER)
    assert policy.shown("workspace_read", PERSON)


def test_the_manifest_carries_a_tools_shared_declaration_and_refuses_another_word():
    from vexa_mcp.manifest import ManifestError, assemble
    doc = {"contract": "mcp.tools.v1", "domain": "agent", "source": "oss", "owner": "core/agent",
           "base_url_env": "AGENT_API_URL", "served_at": "/.well-known/mcp-tools.json",
           "depends_on": ["identity"],
           "tools": [{"name": "workspace_read", "identity": "user", "auth": "subject",
                      "requires": ["agent"], "route": {"method": "GET", "path": "/api/workspace/file"},
                      "shared": "allow"},
                     {"name": "people_followups", "identity": "user", "auth": "subject",
                      "requires": ["agent"], "route": {"method": "GET", "path": "/api/people/followups"},
                      "shared": "refuse"}]}
    asm = assemble([doc], deployed={"agent", "identity"}, env={})
    assert {t.name: t.shared for t in asm.tools} == {"workspace_read": "allow", "people_followups": "refuse"}
    bad = {**doc, "tools": [{**doc["tools"][0], "shared": True}]}
    with pytest.raises(ManifestError):
        assemble([bad], deployed={"agent", "identity"}, env={})
