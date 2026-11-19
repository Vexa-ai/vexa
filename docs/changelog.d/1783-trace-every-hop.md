- **One chat can be followed through every service (#1783).** Every control-plane service now
  writes `logevent.v1` lines with a shared `trace_id`: agent-api, the agent worker, the MCP
  service, identity, flows-api, flows-worker and the mailbox, alongside the gateway, meeting-api,
  runtime and the credential broker. A turn's trace reaches its worker and goes out on the worker's
  tool calls. Lines a chat or background job causes also carry `session`, the agent unit. It is
  signed into the worker's delegation token, so a worker cannot claim another session. The
  credential broker records the same value in `audit.session` and refuses an agent call that names
  any other session. Contracts: `delegation.v1` gains `unit`, identity.v1 `ValidateDelegation` and
  `gateway-identity.v1` `delegation` gain `unit` (sent as `x-user-delegation-unit`), and
  `logevent.v1` gains `session`. All are optional, back-compatible fields. See
  [Tracing a session](/security-compliance#tracing-a-session).
