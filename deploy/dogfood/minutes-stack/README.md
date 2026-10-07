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

The agent domain owns connection request/status semantics. The gateway assembler
loads its manifest independently from meetings. Before moving a Minutes worker
to that assembled surface, preserve its delegated identity, expiry/revocation,
workspace isolation, autonomous restrictions, and existing tool capabilities.
Do not substitute a service API key for a user's scoped delegation to make a
routing test pass.

## Agent MCP service

The Minutes deployment supplies `agent_mcp.py` with one explicit JSON configuration:
`environment_file`, `runtime`, `crm_tools`, `import_tools`, `connection_tools`,
`agent_source`, `host`, and `port`. The runtime bundle preserves existing scoped
handlers; the composition root adds the agent-owned connection module. Every
input belongs in the deployment lock. This is not a rewritten legacy tool engine.

`AGENT_MCP_URL` on gateway selects that dedicated upstream only for `vxd_`
delegated requests to exact `/mcp`. The edge forwards the bearer with transport
headers and strips asserted identity, cookies and operator keys. The agent service
validates signature, expiry and revocation and enforces scope/regime. It is not
an API-key exchange; delegated tokens never authorize gateway REST requests.
API-key MCP callers retain the separately configured `MCP_URL` upstream.

Configure workers with gateway's `/mcp` URL. Validate the actual worker's image,
URL and tool call before retiring the previous service; pre-existing workers
keep the environment they were launched with and must drain first.
