# tools-seed — the unit toolbelt registry

One JSON per tool, loaded by `ToolRegistry.from_dir` into a claude grant (`--allowedTools` + an
injected `.mcp.json`). Each file is `{ "tool": <tool.v1>, "mcp": { "command", "args", "env" | "url",
"type" } }` — the `tool` half is the governance descriptor (grant `auto`/`gate`, cred ref, barriers),
the `mcp` half is how to launch the server. A unit's `unit.v1.tools` names select from here; `auto`
tools enter the allow-set, `gate` tools attach but are withheld for per-call approval. Adding a tool
(email, calendar, … thousands) is a descriptor here, never agent-api code. The email tool lands with
the MVP3 email-service.
