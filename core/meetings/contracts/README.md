# contracts — published by `meetings`

Language-neutral contracts this domain owns (transcript · lifecycle · acts · invocation ·
service-authority · session-profile, the authenticated bot's stored session and its write-back · the
native SDK's sdk-join and sdk-capture IPC). `mcp.tools.v1`, the manifest each domain gives the MCP edge,
is owned by the gateway: [`core/gateway/contracts/mcp.tools.v1`](../../gateway/contracts/mcp.tools.v1/). Consumers reference them across the boundary (the legitimate seam); a domain
may depend on another domain's `contracts/` but never its `services/`/`modules/`. `gate:schema`
validates goldens ≡ schema.
