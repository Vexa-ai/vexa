- **Features that are built but not yet proven are switched off by default (#1783).** Shared chats
  with other people, notifications, calls, the CRM, browser sessions and bot accounts, dropping a
  folder, the agent reading your other chats, calendar changes and mail drafts, forgetting a file,
  and agent-to-agent messages are each one switch. Off, the app does not show them, the agent is not
  offered their tools, and their routes answer `feature_disabled`. Nothing they stored is removed.
  An install switches them on with `VEXA_FEATURES` (`all`, or a list such as `calls,notifications`),
  and the administrator can switch each one on or off in `_global/POLICIES.md` with
  `features: {calls: on}`. See [Configuration](/configuration#feature-switches).
