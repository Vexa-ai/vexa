- **Agents get the workspace, claim, membership and Highlight tools on every install (#1784).** The
  gateway's MCP server now serves `workspace_write`, `workspace_delete`, `workspace_move`,
  `entity_upsert`, `fetch_asset`, `propose`, `validate` (a new `POST /api/claims/verdicts`),
  `workspace_members`, `workspace_invite`, `workspace_membership`, `mark_global_ready` and
  `transcript_terms` (a new `POST /api/meeting/terms/scan`), so Extend, Highlight, the desk setup
  card, the member buttons and the post-meeting write-up work outside the hosted rig. A page write
  from an agent with no `slug` lands in the workspace its chat is working in (`personal` names the
  person's own desk), and a bot an agent sends from a group chat is that group's meeting. Prompts
  read transcripts with `get_meeting_transcript` and send bots with `request_meeting_bot`. **For
  scripts:** `POST /api/workspace/move` names the page's current path `path` (was `from`). See
  [MCP server](/vexa-mcp) and the [agent API](/api/agent).
