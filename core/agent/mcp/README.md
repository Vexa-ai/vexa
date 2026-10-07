# Agent MCP adapters

Agent-owned tools for Connections, source research, current time/timezone and chat naming.
The registration functions receive the MCP server and authenticated caller/runtime adapters.
They preserve the caller identity and delegate to agent-api; secret values are entered through
the trusted Connections UI and resolved by the credential broker, never returned to the model.

The gateway routes delegated agent MCP traffic separately from meeting API-key MCP traffic.
The Minutes composition root is documented under deploy/dogfood/minutes-stack. Tool manifests
live in core/agent/mcp.tools.v1.json; tests in core/agent/tests/test_mcp_connections.py,
test_time_context.py and test_chat_names.py exercise the adapters.
