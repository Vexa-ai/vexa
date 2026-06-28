"""tools.py — the generic toolbelt mechanism (tool.v1 → a claude grant).

agent-api is TOOL-AGNOSTIC: it knows email/calendar/news/… not at all. Every capability is a ``tool.v1``
descriptor (name, grant auto|gate, transport mcp, the MCP server that backs it, governance barriers)
plus an MCP *launch* spec (how to start that server) — thousands of them, all loaded the same way. A
unit's scoped toolbelt (``unit.v1.tools`` = tool.v1 names) resolves HERE into the two levers Claude Code
understands: the ``--allowedTools`` allow-set and an ``.mcp.json`` (``--mcp-config``) that attaches the
servers. ``auto`` tools enter the allow-set; ``gate`` tools attach but are withheld from the allow-set
(a per-call approval is surfaced as a proactive-card.v1 action — the human gate). Unknown names are
fail-closed (never granted). This is the one place a tool becomes real for the unit; adding a tool is a
descriptor, not code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import contracts


@dataclass
class ToolGrant:
    """The resolved grant for a unit's toolbelt — exactly what the runner hands Claude Code."""
    allowed_tools: list[str] = field(default_factory=list)   # mcp__<server> entries for --allowedTools
    mcp_servers: dict = field(default_factory=dict)          # .mcp.json mcpServers (server id → launch)
    gated: list[str] = field(default_factory=list)           # attached but withheld (per-call approval)

    @property
    def has_mcp(self) -> bool:
        return bool(self.mcp_servers)

    def mcp_config(self) -> dict:
        return {"mcpServers": self.mcp_servers}


class ToolRegistry:
    """Resolves tool.v1 names → a ToolGrant. Built from a directory of tool specs (one JSON per tool:
    ``{"tool": <tool.v1>, "mcp": {"command","args","env"|"url","type"}}``) — config, not code."""

    def __init__(self, specs: Optional[dict] = None) -> None:
        self._specs = specs or {}

    @classmethod
    def from_dir(cls, path: str | Path) -> "ToolRegistry":
        specs: dict = {}
        p = Path(path)
        if p.exists():
            for f in sorted(p.glob("*.json")):
                spec = json.loads(f.read_text())
                tool = spec["tool"]
                contracts.validate_tool(tool)        # fail loud on a non-conformant descriptor (P8)
                specs[tool["name"]] = spec
        return cls(specs)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def resolve(self, names: Iterable[str]) -> ToolGrant:
        grant = ToolGrant()
        for name in names:
            spec = self._specs.get(name)
            if not spec:
                continue                              # fail-closed: an ungranted/unknown tool is silent
            tool = spec["tool"]
            mcp = spec.get("mcp")
            if tool.get("transport") == "mcp" and mcp:
                server = tool.get("mcp_server", name)
                grant.mcp_servers[server] = {
                    k: v for k, v in mcp.items() if k in ("command", "args", "env", "url", "type")
                }
                if tool.get("grant") == "auto":
                    grant.allowed_tools.append(f"mcp__{server}")   # allow every tool on this server
                else:
                    grant.gated.append(name)          # attached, but held for per-call approval
        return grant


def apply_tool_grant(
    ws: Path, tools: Iterable[str], registry: Optional[ToolRegistry]
) -> "tuple[list[str], Optional[str]]":
    """Resolve a unit's toolbelt (unit.v1.tools names) onto a workspace → (--allowedTools, an injected
    .mcp.json path or None). The mcp config is written under ``.claude/`` so it is never staged/committed
    by the governance layer. Defaults to Read/Write/Edit; unknown tools are fail-closed (dropped).
    (The single place a tool grant becomes real for a turn — used by the worker's chat/meeting runners.)"""
    allowed = ["Read", "Write", "Edit"]
    names = list(tools)
    if registry is None or not names:
        return allowed, None
    grant = registry.resolve(names)
    allowed += grant.allowed_tools
    if not grant.has_mcp:
        return allowed, None
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    mcp_path = ws / ".claude" / "mcp.json"
    mcp_path.write_text(json.dumps(grant.mcp_config()))
    return allowed, str(mcp_path)
