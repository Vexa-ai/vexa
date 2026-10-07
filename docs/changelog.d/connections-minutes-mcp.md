Minutes can open a trusted Gmail or Google Calendar consent panel when agent MCP
requests a connection, including repeated requests for an existing pending setup.
The tools expose metadata only; user tokens stay in the credential broker.
Gateway routes delegated worker MCP traffic to the separately configured agent
MCP service, which validates delegation and enforces workspace/regime restrictions.
The existing API-key MCP route remains separate. Requires optional broker and
AGENT_MCP_URL configuration.

Connections management opens as a central page with responsive service cards and
expandable accounts. Agent-requested setup uses the same controls in the existing
right panel, focused on the requested service. Closing management or selecting a
chat restores the conversation and reader without remounting them.
