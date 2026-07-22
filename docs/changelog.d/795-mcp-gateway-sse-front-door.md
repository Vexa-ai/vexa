- **MCP clients connect through the gateway front door, and the SSE stream stays open (#795).** The
  gateway now fronts the MCP streamable-HTTP transport at `/mcp`: `POST` (messages) forwards
  buffered as before, and `GET` — the server→client SSE stream — is **relayed** on a dedicated
  streaming client with no read deadline, because a stream that is silent is a stream with nothing
  to push. Buffering that leg is what made a healthy stream look like a dead upstream: the proxy
  waited on the next body read, hit its 30-second read timeout, and answered a `503` the MCP
  service never saw (8 of 14 stream-open attempts in a 15-minute hosted window, while `POST` was
  116/116 healthy on the same client). Point your MCP client at your Vexa API host — `mcp-remote
  https://<your-api-host>/mcp --header "Authorization: Bearer $VEXA_API_KEY"` — instead of the
  MCP service's own port. The gateway carries the upstream's status, content type and
  `mcp-session-id` verbatim; it never rewrites an MCP answer.
