- **MCP tool visibility per deployment and per person (#1783).** `VEXA_MCP_TOOLS_ALLOW` limits the
  tools an ordinary caller is shown in `tools/list` and may call, `VEXA_MCP_TOOLS_DENY` withholds tools
  from everyone, and `VEXA_MCP_TOOLS_COHORT` names the people shown every tool. A hidden tool called by
  name is answered as an unknown tool. Unset, nothing changes. Helm: `mcp.tools.{allow,deny,cohort}`.
