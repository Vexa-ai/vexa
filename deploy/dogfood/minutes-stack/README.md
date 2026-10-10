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
| `agent_source` | the `core/agent` tree: exported as `VEXA_AGENT_SRC`, and its `mcp.tools.v1.json` supplies the declared `forward` (boot stops without one) |
| `host`, `port` | where the server listens. Exported as `VEXA_MCP_LISTEN_HOST` so the transport's host guard admits workers calling the in-network address; never published |
| `public_url` | **required.** The https address people reach this service at, ending in `/mcp` (e.g. `https://mcp.example.com/mcp`); exported as `VEXA_PUBLIC_MCP_URL`. Every link the rig hands a person is built on it: the sign-in page, the connect command, workspace view links. The boot stops (P18) when it is missing, not https, or names a private, loopback, link-local or single-label host (`../rig/public_origin.py`). No http exception: a test that needs a loopback address calls the rig directly |

`agent_tools.register(mcp, call=…, guard=…)` takes two explicit ports: `call` reaches
agent-api as the caller THROUGH THE GATEWAY, so agent-api receives the gateway's signed
identity and can forward it to the credential broker, which acts for a person on nothing
else; `guard` is the rig's identity guard. A person goes with their own gateway key to the
REST route, mapped by the `forward` the agent manifest declares (`agent_forward`; the adapter
spells no `/agent/` ↔ `/api/` of its own). A worker's delegation token (`vxd_`) is an MCP credential the gateway
admits on `/mcp` only, so a worker's call goes there as itself, to the product tool of the same
name (each `call` names it; a test holds every name, route and argument to
`core/agent/mcp.tools.v1.json`), and identity resolves its regime and ceiling. Consent,
credential use, the regime refusals and the ceiling are agent-api's, not the adapter's.

The gateway's `/mcp` must be the product MCP service, not this one: a worker's call that comes
back here through the gateway carries this process's hop marker and is refused (508) rather than
calling itself. The gateway MCP bounds a tool call at 30 s, which is shorter than the
`onboarding_research` batch timeout a person's REST call gets.

### What Minutes needs after the identity change

- The gateway has ONE `/mcp` upstream (`MCP_URL`); `AGENT_MCP_URL` is gone. Point
  agent-api's `VEXA_MCP_URL` at this service's own in-network `/mcp` to keep workers on
  the rig, or at the gateway's `/mcp` to move them to the assembled surface. That surface
  serves the agent domain's tools (`core/agent/mcp.tools.v1.json`: `workspace_write`,
  `entity_upsert` and `propose` among them) but not the rig-only ones (`open_page`,
  `workspace_target`, the meeting and auth verbs; `RIG_ONLY` in
  `core/agent/tests/test_prompt_tools_served.py`).
- Replace `connection_tools`, `time_tools` and `chat_names` in the lock with `agent_tools`.
- Add `public_url` to the agent-mcp configuration before restarting it: the service refuses to
  boot without one. The OAuth resource this service advertises becomes that address, so an OAuth
  token issued against the old listen address stops resolving and that client signs in again.
- Hosted workers are refused the external sign-in verbs (`auth_link`, `auth_claim`,
  `start_onboarding`, `confirm_login`) with `already_signed_in` and are not offered them
  ([`../rig/README.md`](../rig/README.md#sign-in-for-external-mcp-clients)).
- The gateway's identity keypair: `VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE` on the gateway
  only, `VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE` on agent-api, meeting-api and the credential
  broker; and `VEXA_MCP_DELEGATION_SECRET` on admin-api as well as agent-api and this service
  (admin-api resolves the delegation tokens this service forwards to the gateway).
- Workers' Connections, clock and chat-naming calls reach agent-api through the gateway's
  `/mcp` (see above), so the gateway's `MCP_URL` must be the product MCP service with the agent
  domain assembled.
- A worker files friction through the gateway (`/agent/friction`, its delegation token),
  derived from `VEXA_MCP_URL`; a worker pointed at the rig keeps the record in its
  fallback log instead.

Validate the actual worker's image, URL and tool call before retiring the previous
service; pre-existing workers keep the environment they were launched with and must drain
first.
