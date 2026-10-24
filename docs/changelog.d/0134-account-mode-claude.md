- **«Vexa for Claude» accounts run no Vexa agent.** An account created through the Claude plugin or
  connector is stamped `account_mode: claude` (identity.v1). For it, Vexa spends no model: agent turns
  of every trigger (chat, routines, event agents, mail, post-call write-ups, meeting signals) are
  refused before dispatch, the meeting listener does not score its calls, and Home ranking, chat
  titles, the intent router and entity summaries make no model call. Calls (bot, transcript,
  recording, calendar auto-join) and workspaces work as before; the terminal shows Calls and
  Workspaces in place of the agent chat. Existing accounts keep `standard`; an operator moves an
  account with `PATCH /admin/users/{id}` `{"account_mode": "claude" | "standard"}`.
