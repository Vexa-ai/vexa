- **Your desktop is stored per person, and you can ask the agent to arrange it (#1783).** With the
  `desktop` feature on (it needs `crm`), crm-api keeps each person's placed agents and files, every
  arrangement as an append-only version with Undo, and suggestions an agent was not asked for.
  New tools `desktop_read`, `desktop_arrange` and `desktop_arrange_propose`; the gateway fronts
  `/crm/desk/read`, `/crm/desk/arrange`, `/crm/desk/proposal` and `/crm/desk/undo`. An agent changes
  the desktop directly only in a turn the person typed in their own chat; otherwise it suggests.
  The CRM schema moves to version 3 (three new tables; existing rows untouched). Off by default.
- **The desktop's limits are company policy (#1783).** Four `_global/POLICIES.md` keys,
  `desk_placements_max` (50), `card_proposals_pending_max` (5), `card_proposals_per_agent_per_day` (20)
  and `card_proposal_ttl_days` (7), with today's limits as defaults. A company can only narrow them; a
  larger or unreadable value is the default and is logged. crm-api reads the file through agent-api
  (new optional `CRM_AGENT_API_URL`, default `http://agent-api:8100` in compose, set by the chart when
  agent-api is on), at most every 30 seconds, and keeps the defaults when agent-api is absent.
