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

## Worker tools

Minutes' workers get their tools the way every deployment's do: the gateway's one `/mcp`,
assembled from each domain's `mcp.tools.v1` manifest (meetings, identity, agent, flows). The
control rig and the `agent_mcp.py` composition root that loaded it were removed
(Vexa-ai/vexa#1783). Moving a running stack off them, with rollback, is
[`MIGRATION-app-dev.md`](MIGRATION-app-dev.md).

The gateway's identity keypair: `VEXA_GATEWAY_IDENTITY_SIGNING_KEY_FILE` on the gateway only,
`VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE` on agent-api, meeting-api and the credential broker; and
`VEXA_MCP_DELEGATION_SECRET` on admin-api as well as agent-api (admin-api resolves the delegation
tokens agent-api mints).
