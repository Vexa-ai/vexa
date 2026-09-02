# `vexa_mcp` — the edge, module by module

Read it in this order; each layer only knows the one below it.

| module | what it is |
|---|---|
| `config.py` | Every deployment input, each read as a literal so `gate:config-contract` can see it. Sibling URLs, credentials, and `VEXA_HOME` — the one directory this service writes. |
| `httpc.py` | The only way out of the process: four doors (gateway, agent-api, admin-api, flows-api) plus the mail double. Identity travels, credentials do not. |
| `delegation.py` | `core/agent/shared/delegation.py`, VERBATIM. The `vxd_` token agent-api mints per dispatch is verified with the library, not with a second hand-rolled HMAC. Byte identity is asserted by a test. |
| `identity.py` | WHO IS CALLING, resolved once. Durable token, delegation token, or the registration link — three doors, one answer, and one `anon_guard` that turns "no account" into guidance instead of a stack trace. |
| `shaping.py` | Turning a service's answer into a tool's answer: response budgets that trim DATA and never the text, link minting, the credential refusal, meeting-reference resolution. |
| `instructions.py` | The instruction string every client shows before any tool is called. One file, because it used to be a literal in the middle of five thousand lines. |
| `registry.py` | How a domain says "this function is a tool", without importing a server. |
| `prompts.py` | The three prompts a person sees in their client's slash menu. |
| `server.py` | The MCP server: instructions + 64 tools + 3 prompts, over a stateless streamable-HTTP transport. |
| `web.py` | The ASGI surface around the transport: bearer auth, the sign-in pages, the invite redemption, the workspace file view. A person clicking a link is not an agent calling a verb, so none of it is a tool. |
| `oauth.py` | The OAuth handshake a client may opt into, and the token resolution behind it. |
| `cli.py` | `vexa-mcp`. Streamable HTTP is the product path; `--stdio` is a test transport. |
| `tools/` | The 64 tools, one module per domain. See `tools/README.md`. |
