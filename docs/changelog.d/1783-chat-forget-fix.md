- **Chat reading and forget fixes behind their switches (#1783).** With `chat_history_tools` on,
  `chats_search` with no words lists the newest chats, `chat_read` reads every chat on the
  person's rail (including chats recorded before the session store, by their owner only and never
  in a shared chat), takes a `c:` row from `chat_folders_list` (which now also gives each chat's
  `session`), and returns what the person typed without the context line the terminal adds. With
  `workspace_forget` on, a named file that is no longer on disk is still removed from history and
  transcripts using its last committed version, and is reported as already gone rather than failing.
  The gateway now answers a tool call on a route it does not list for tools with
  `route_not_listed_for_tools`, which says the gateway needs rebuilding.
