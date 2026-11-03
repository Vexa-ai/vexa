"""MVP3 generic tool mechanism — tool.v1 → a claude grant (--allowedTools + .mcp.json).

Proves agent-api is TOOL-AGNOSTIC: a unit's toolbelt (unit.v1.tools names) resolves to the two levers
Claude Code understands; auto tools enter the allow-set, gate tools attach but are withheld; unknown
names are fail-closed; and the chat runner injects an .mcp.json under .claude (never committed) +
passes --mcp-config. No tool's behaviour is hardcoded anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import contracts
from llm.claude_code import build_argv
from shared.tools import ToolRegistry, apply_tool_grant

_EMAIL_TOOL = {
    "tool": {
        "name": "email", "scope": "inbox:read,draft:write", "grant": "auto",
        "cred_ref": "secret://email/oauth", "transport": "mcp", "mcp_server": "vexa-email",
    },
    "mcp": {"command": "python", "args": ["-m", "vexa_email_mcp"], "env": {"EMAIL_SERVICE_URL": "http://email:8200"}},
}
_SEND_TOOL = {
    "tool": {
        "name": "email.send", "scope": "send:self", "grant": "gate",
        "cred_ref": "secret://email/oauth", "transport": "mcp", "mcp_server": "vexa-email-send",
        "barriers": ["info-barrier"],
    },
    "mcp": {"command": "python", "args": ["-m", "vexa_email_send_mcp"]},
}


def _registry(tmp_path: Path) -> ToolRegistry:
    d = tmp_path / "tools-seed"
    d.mkdir()
    (d / "email.json").write_text(json.dumps(_EMAIL_TOOL))
    (d / "email.send.json").write_text(json.dumps(_SEND_TOOL))
    return ToolRegistry.from_dir(d)


def test_tool_descriptors_are_conformant():
    contracts.validate_tool(_EMAIL_TOOL["tool"])
    contracts.validate_tool(_SEND_TOOL["tool"])


def test_auto_tool_enters_allowset_gate_tool_is_withheld(tmp_path):
    reg = _registry(tmp_path)
    grant = reg.resolve(["email", "email.send"])

    # auto email tool → allow-set + attached server; gate send tool → attached but NOT allowed.
    assert "mcp__vexa-email" in grant.allowed_tools
    assert "mcp__vexa-email-send" not in grant.allowed_tools
    assert "email.send" in grant.gated
    assert set(grant.mcp_servers) == {"vexa-email", "vexa-email-send"}      # both ATTACHED
    assert grant.mcp_servers["vexa-email"]["command"] == "python"


def test_unknown_tool_is_fail_closed(tmp_path):
    grant = _registry(tmp_path).resolve(["email", "nope"])
    assert "nope" not in grant.gated
    assert set(grant.mcp_servers) == {"vexa-email"}                          # the unknown is silently dropped


def test_build_argv_attaches_mcp_config():
    argv = build_argv("hi", allowed_tools=["Read", "Write", "mcp__vexa-email"], mcp_config="/ws/.claude/mcp.json")
    assert "--mcp-config" in argv and "/ws/.claude/mcp.json" in argv
    assert "--strict-mcp-config" in argv
    i = argv.index("--allowedTools")
    assert "mcp__vexa-email" in argv[i + 1]


def test_apply_tool_grant_writes_its_mcp_config_outside_the_workspace(tmp_path):
    """An MCP server's `env` can carry a credential, and the workspace is read through every file
    route: the config lands in a private directory of this process's own, never under `ws`."""
    import stat as _stat
    from pathlib import Path
    ws = tmp_path / "ws" / "u_jane"
    ws.mkdir(parents=True)
    allowed, mcp_config = apply_tool_grant(ws, ["email"], _registry(tmp_path))

    mcp = Path(mcp_config)
    assert mcp.exists() and "vexa-email" in json.loads(mcp.read_text())["mcpServers"]
    assert ws.resolve() not in mcp.resolve().parents                         # not in the workspace
    assert not (ws / ".claude" / "mcp.json").exists() and not (ws / "mcp.json").exists()
    assert _stat.S_IMODE(mcp.stat().st_mode) == 0o600
    assert _stat.S_IMODE(mcp.parent.stat().st_mode) == 0o700
    assert "mcp__vexa-email" in allowed
    # and it wires through build_argv exactly as the runner used to.
    argv = build_argv("triage", allowed_tools=allowed, mcp_config=mcp_config)
    assert "--mcp-config" in argv and str(mcp) in argv
    assert "mcp__vexa-email" in argv[argv.index("--allowedTools") + 1]
