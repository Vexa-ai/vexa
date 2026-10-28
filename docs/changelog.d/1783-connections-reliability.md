- **Connection calls say which service failed, and a re-created connection is found again (#1783).**
  A slow call to a connected service used to surface as a bare `upstream timeout`. Every hop now
  gives up before the one that called it — the broker's call to the service within 12 s, agent-api's
  whole budget for the broker within 25 s, the gateway's forward at 30 s, the MCP tool call at 35 s —
  and each fault carries a `hop` (for example `credential-broker → connected service`). A read is
  tried once more after a timeout or an outage; a POST that may have arrived is never repeated. A
  connection the person deleted answers `410` naming the connection they created in its place
  (`reason: connection_replaced`, `replaced_by`), instead of `404 Connection not found`. When no
  account of a kind is ready, the account tools say which setup waits for the person
  (`reason: not_ready`) and that they finish it in Connections.
