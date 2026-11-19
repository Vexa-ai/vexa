- **Agent-requested connections open in the right panel (#1784).** When an agent asks for a Gmail or
  Google Calendar connection, Minutes opens the consent panel for that service, including a repeated
  request for a setup that is already pending. The tools return metadata only; the person's tokens
  stay in the credential broker. The Connections tools are served by the gateway's one MCP server to a
  person's own client and to every agent worker, and a worker dispatched without a person in the loop
  is refused the ones that read, draft or spend a credential. Closing Connections or selecting a chat
  restores the conversation and the reader without reloading them. See [Connections](/connections).
