- **The gateway admits an agent worker's MCP tool calls only on the routes those tools call (#1784).**
  A worker's token, coming back through the gateway on the MCP's behalf, is accepted only on a route
  whose `routes.v1` row says `"mcp_reentry": true`; every other route refuses it with `403`.
- **MCP meeting tools check `platform` and send each id as one path segment (#1784).** A meeting tool
  called with a `platform` other than `google_meet`, `teams`, `zoom` or `jitsi` answers `422` naming
  the valid values, before anything is sent, and every id a tool puts in a URL path is sent
  percent-encoded as exactly one segment.
