- **One MCP: the control rig's remaining verbs move to the gateway's `/mcp`, and the rig is removed (#1783).**
  New meeting tools: `get_meeting`, `get_meeting_participants` and `delete_meeting`.
  `delete_meeting` is irreversible: it needs `asked_by_a_human=true`, and a worker with no person
  present is refused.
  With the agent domain deployed, these tools are added: `workspace_attach`, `workspace_push`,
  `workspace_pull`, `open_page`, `workspace_target` and `company_context`.
  With identity deployed, these are added: `settings`, `settings_set`, `workspace_regime` and
  `workspace_regime_set`, backed by the new person-scoped `GET`/`PUT /user/settings` and
  `/user/workspace-regime` routes.
  Each tool forwards to its domain's route as the caller. No tool takes a credential argument.
  The meetings domain now serves its own tool manifest (`core/meetings/mcp.tools.v1.json`, at
  meeting-api's `/.well-known/mcp-tools.json`); `get_bot_status` and `get_recording` come from it,
  unchanged for callers.
  The dogfood control rig (`deploy/dogfood/rig/`) is removed. In-conversation sign-in is dropped:
  sign-in happens at the edge. `bot_schedule` returns as a flows schedule trigger.
  See [Vexa MCP](/vexa-mcp).
