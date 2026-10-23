- **Choose who is served the agent (#1783).** `VEXA_AGENT_ALLOW` (Helm `gateway.agentAllow`) lists the
  user ids, addresses and `@domain` entries agent-api serves; anyone else is refused
  `agent_tier_not_enabled`. Empty (the default) serves everyone, as before.
