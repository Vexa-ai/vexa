# Minutes deployment verification

`python stack.py /private/path/deployment.lock.json` observes the declared containers,
host services, immutable image IDs, source/configuration file hashes, worker routing,
and actual worker MCP tool discovery. It rejects invalid credentials as a negative
control. It never invokes tools or changes services. Exit 0 means all declared
checks passed; 1 means drift/missing capability; 2 means observation failed.

The private deployment lock records ordered Compose inputs, explicit service
entrypoints, and the desired capability list. Never commit environment files,
credentials, or host-specific configuration to this directory. Locks are written
by the deployment owner after review, never automatically refreshed by verification.

`python compose.py /private/path/deployment.lock.json minutes terminal` previews
the exact Compose command. `--apply` applies only named services, without pulling
images or restarting dependencies. The owner's lock includes a final image-ID
overlay so a moved local tag cannot silently select a different build. Run the
read-only verifier after deployment. Updating source/image inputs requires an
explicitly reviewed new lock; verification never blesses observed drift.

Run `python -m unittest discover -s deploy/dogfood/minutes-stack` for failure controls.
Use `--inventory` for sanitized evidence. A locked legacy route is still legacy:
tool discovery does not prove domain separation, tool execution, consent, or sync.

The agent domain owns its tools: each is a typed agent-api route in
`core/agent/mcp.tools.v1.json`, served on a standard deployment by the gateway's one
assembled MCP server. Minutes still gives its workers the rig as their MCP server, so
`agent_tools.py` registers the same tool names on the rig and calls those same routes.

## Agent MCP service

`agent_mcp.py` reads one JSON configuration, every input of which belongs in the
deployment lock:

| key | what it is |
|---|---|
| `environment_file` | JSON environment; its `VEXA_*`, `CRM_*` and `INTERNAL_API_SECRET` entries are exported |
| `runtime` | the rig module (`deploy/dogfood/rig/vexa_control_mcp.py`) — the delegated identity and the rig's own tools |
| `agent_tools` | `agent_tools.py` — the agent domain's tools (Connections, `current_time`, `timezone_set`, `chat_name`) and the clock folded into `whats_waiting` |
| `import_tools` | the rig's `workspace_import_tools.py` |
| `crm_enabled`, `crm_tools` | optional CRM tools (`CRM_API_URL` must be in the environment) |
| `agent_source` | exported as `VEXA_AGENT_SRC` |
| `host`, `port` | where the server listens; `VEXA_PUBLIC_MCP_URL` is derived from them |

`agent_tools.register(mcp, call=…, guard=…)` takes two explicit ports: `call` reaches
agent-api as the caller (the rig's `_http` adds `X-Internal-Secret` and the delegation's
regime and workspace ceiling to every call that names a person — agent-api believes an
unsigned `X-User-Id` from nothing else) and `guard` is the rig's identity guard. Consent,
credential use, the regime refusals and the ceiling are agent-api's, not the adapter's.

### What Minutes needs after the identity change

- The gateway has ONE `/mcp` upstream (`MCP_URL`); `AGENT_MCP_URL` is gone. Point
  agent-api's `VEXA_MCP_URL` at this service's own in-network `/mcp` to keep workers on
  the rig, or at the gateway's `/mcp` to move them to the assembled surface (which does
  not carry the rig-only tools: `workspace_write`, `entity_upsert`, `propose`, …).
- Replace `connection_tools`, `time_tools` and `chat_names` in the lock with `agent_tools`.
- `VEXA_GATEWAY_IDENTITY_SECRET` on gateway, agent-api and meeting-api, and
  `VEXA_MCP_DELEGATION_SECRET` on admin-api as well as agent-api and this service.
- A worker files friction through the gateway (`/agent/friction`, its delegation token),
  derived from `VEXA_MCP_URL`; a worker pointed at the rig keeps the record in its
  fallback log instead.

Validate the actual worker's image, URL and tool call before retiring the previous
service; pre-existing workers keep the environment they were launched with and must drain
first.
