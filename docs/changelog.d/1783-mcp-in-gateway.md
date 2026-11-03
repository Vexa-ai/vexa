- **The MCP service lives in the gateway module (#1783).** Its source moved from
  `core/meetings/services/mcp` to `core/gateway/services/mcp`, and the tool-manifest contract
  `mcp.tools.v1` to `core/gateway/contracts/` — it assembles one MCP from every module's manifest,
  so it belongs to the edge, not to meetings. Behaviour, tools, port and container are unchanged;
  only builds from source see the new path.
