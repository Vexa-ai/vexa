- **Lite: the agent's tools are served again (#1784).** The Lite image now carries the agent
  domain's MCP tool manifest, so its agent-api answers `/.well-known/mcp-tools.json` instead of 503
  and the MCP service starts. A test derives every file a Lite service reads beside its own code and
  fails the build when the image does not put it there.
- **Codex subscription credentials reach the Codex harness (#1784).** The runtime mounts the
  `auth.json` given in `HOST_CODEX_CREDENTIALS` into the worker's Codex home and names it
  (`CODEX_HOME`, `/tmp/.codex`); the harness and the Codex CLI read the same variable. On Kubernetes,
  mount the Secret at `/tmp/.codex/auth.json`.
