- **Chats are stored apart from workspaces, and you can add people to one (#1783).** A conversation is
  now kept by agent-api, in the instance's Postgres (its own role, grants on `agent_session*` only)
  and, per turn, in its own bucket `vexa-sessions` with a key scoped to it, instead of in a hidden
  folder of your workspace. On by default in Compose and Lite (Lite with object storage); on Helm you
  provide the bucket and key. The owner can add a colleague by email from the bar above the chat.
  Adding someone shares the conversation, never a workspace. With two or more people the agent answers
  when mentioned (`@vexa`; the `session_agent_speaks` policy changes this) and uses only what every
  participant can read: its worker is refused your mail, calendar, other connections, meetings and
  flows (`shared_conversation`). A chat runs one turn at a time (a second is `409 session_conflict`),
  and a reply the store refused shows as a fault in the chat until the next one saves. Existing chats
  keep working and move into the store on their next turn, or all at once with
  `python -m control_plane.session_migrate`. See [Shared chats](/minutes/shared-chats).
