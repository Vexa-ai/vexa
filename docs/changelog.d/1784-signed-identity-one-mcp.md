- **The gateway signs the identity it forwards, and agent-api and meeting-api check it (#1784).** Every
  request the gateway forwards carries `X-Vexa-Identity`, an HMAC over the user it resolved; agent-api
  and meeting-api refuse a user header without it, other than from Vexa's own services holding
  `INTERNAL_API_SECRET`. **Upgrading:** `VEXA_GATEWAY_IDENTITY_SECRET` is new and required on the
  gateway, agent-api and meeting-api — `make up` (`mint-dev-env.sh`), the Helm chart and Lite generate
  it; with a pre-created Helm Secret, add it yourself. `VEXA_REQUIRE_GATEWAY_IDENTITY` and
  `VEXA_AGENT_DEFAULT_SUBJECT` are gone: a request to agent-api that names nobody is a `401`. Call
  agent-api through the gateway (`/agent/*` with an API key). See [Identity](/core/identity).
- **Agent workers get the toolbelt on every install (#1784).** Each worker reaches the gateway's one MCP
  server with a delegation token minted for its dispatch — the meetings, agent and flows tools, including
  `request_meeting_bot`, Connections, `current_time` and `chat_name`. `VEXA_MCP_DELEGATION_SECRET` is
  generated at install (agent-api and admin-api); the Helm chart now deploys the MCP service, and Lite runs
  it. A spawn without a toolbelt says so in its log. See [MCP server](/vexa-mcp).
- **Helm: a default NetworkPolicy for agent-api and meeting-api (#1784).** Only the services that call
  them can reach them, and meeting bots keep their callbacks; agent workers reach neither.
  `networkPolicy.enabled: false` turns it off. See [Kubernetes](/deployment-kubernetes).
