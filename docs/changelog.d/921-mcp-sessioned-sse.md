- **MCP sessioned GET /mcp starts its SSE stream (#921).** A `GET /mcp` with a valid
  `mcp-session-id` now emits `text/event-stream` headers promptly (and sse-starlette's
  keep-alive ping on an idle stream). fastapi-mcp's buffered HTTP adapter had swallowed
  the open SSE response so the server→client channel never opened. Sessionless GET still
  returns MCP's own 400.
