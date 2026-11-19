- **The agent tier can be enabled per person (#1783).** `VEXA_AGENT_ALLOW` on the gateway names the
  user ids, emails or `@domain`s the agent tier is enabled for (empty: everyone, as before). Anyone
  else is answered `403 agent_tier_not_enabled` on every `/agent/*` route before anything is
  forwarded, including the MCP's agent tools; meetings, transcripts and bots are unchanged.
