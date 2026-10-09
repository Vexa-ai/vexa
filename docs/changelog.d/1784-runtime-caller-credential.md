- **The runtime answers only the control plane (#1784).** Every runtime route except `/health` now
  requires a caller credential, `RUNTIME_API_TOKEN`, which agent-api and meeting-api present and no
  bot or agent worker holds. The runtime, agent-api and meeting-api refuse to boot without one (32+
  bytes, never a placeholder): `make all` mints it into `.env`, the Helm chart generates one and keeps
  it across upgrades (with `secrets.existingSecretName`, add a `RUNTIME_API_TOKEN` key), and Lite mints
  one per boot. The workspace store a worker mounts is now the runtime's own configuration
  (`VEXA_WORKSPACE_MOUNT_SOURCE` / `_TARGET` on the runtime): a spawn request names which workspaces
  a worker sees, never where they come from or how its Pod is scheduled. On Lite the runtime starts
  from a cleared environment and builds each bot's and worker's environment from scratch, and spawned
  Pods no longer mount a ServiceAccount token. See [Configuration](/configuration#secrets--identity).
- **Helm: the runtime and the Pods it spawns are fenced (#1784).** New NetworkPolicies (on with
  `networkPolicy.enabled`) admit only agent-api and meeting-api to the runtime, give spawned bots and
  workers no inbound connection, and let them reach only DNS, public addresses and their own
  dependencies — workers the gateway, redis and flows-api; bots meeting-api and redis. **If your bots
  reach an STT service, S3 or proxy on a private address, or your workers an in-cluster LLM endpoint,
  add it** with `networkPolicy.workloads.bot.extraEgress` / `networkPolicy.workloads.worker.extraEgress`
  before upgrading. See [Kubernetes](/deployment-kubernetes).
