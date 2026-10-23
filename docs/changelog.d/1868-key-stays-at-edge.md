- **Your API key stops at the gateway (#1868).** Services behind the gateway (meeting-api, agent-api,
  the credential broker) now receive only the gateway's signed identity, never your key or bearer
  token. The MCP, identity and crm still receive it, because each still authenticates the key
  itself; moving them onto the signed identity is a follow-up.
