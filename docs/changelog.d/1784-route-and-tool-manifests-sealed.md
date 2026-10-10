- **The route and MCP tool manifests are sealed contracts (#1784).** A private deployment that mounts
  its own `*.mcp.tools.v1.json` now has a published schema to write it against:
  `core/meetings/contracts/mcp.tools.v1`. Each domain's `routes.v1.json` has one too, in
  `core/gateway/contracts/routes.v1`. The MCP's and the gateway's boot checks are unchanged; the
  schemas also refuse a key they do not name, and the repository's own manifests are held to them.
