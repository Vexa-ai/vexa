# deploy/dogfood/bin — the probe

`mcp-validate` drives a RUNNING Vexa MCP endpoint as a real MCP client.

The MCP service's own tests (`core/meetings/services/mcp/tests/`) prove the app in-process against
a faked gateway. That is the right test, and it cannot answer the question an MCP client asks:
*does the transport work through the proxy chain, with a real key, against a real gateway?* This
answers that one.

Stdlib only — no install step; runs from a laptop or from the stack host.

```bash
./mcp-validate --url https://mcp.dev.vexa.ai/mcp --key "$VEXA_API_KEY"
./mcp-validate --url http://127.0.0.1:18456/mcp --json
```

Two families of check, and the split is the point:

**CONFORMANCE — is this an MCP server?** `initialize` · the exact declared tool set · the prompt
catalog · a pure tool call (isolating the transport from the gateway) · a real gateway call · and
a fail-closed check that a bogus key is *rejected* rather than forwarded as an anonymous call.
Drift in the tool set fails either direction: a missing tool is a regression, an undeclared extra
one is a README that stopped being true. **These govern the exit code.**

**NATIVE-READY — is this a connector, or does a human still paste a key?** RFC 9728
protected-resource metadata · the `WWW-Authenticate` challenge on 401 · authorization-server
metadata. These are the mechanical difference between "add a custom MCP server and paste a header"
and what a first-class connector does. They are **expected to fail** until the OAuth work lands;
the run prints them as a measured distance rather than hiding them. `--require-native` makes them
binding once that work is expected to hold.
