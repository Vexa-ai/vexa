- **The gateway signs the identity it forwards, and only the gateway can (#1784).** Every request the
  gateway forwards carries `X-Vexa-Identity`, an Ed25519 signature over the user it resolved, made with
  a private key only the gateway holds. agent-api, meeting-api and the credential broker hold the public
  key: agent-api and meeting-api refuse a user header without the signature, other than from Vexa's own
  services holding `INTERNAL_API_SECRET`, and the credential broker uses a person's connections only
  when agent-api forwards the gateway's signature for that same person. **Upgrading:** the keypair is
  generated at install — compose's `identity-keys` service (the private key in the
  `identity-signing-key` volume, mounted only by the gateway), the Helm chart (an `identity-signing-key`
  Secret and an `identity-public-key` ConfigMap; set `identity.signingKey` when you render with
  `helm template`), and Lite (in its state directory). The gateway needs
  `VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE`; agent-api, meeting-api and the credential broker need
  `VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE`. Connections are reached through the gateway only.
  `VEXA_REQUIRE_GATEWAY_IDENTITY` and
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
