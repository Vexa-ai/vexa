- **Shared workspaces follow their roster while a turn runs.** A member added while an agent turn
  runs is counted by that turn's privacy hold at once, not from the next turn: agent-api re-writes
  each running unit's audience the moment a roster changes, and the worker reads it when it commits.
  A member removed from a workspace is refused at once by the s3 store's fetch and push (403
  `not_a_member`, or `read_only` for one made a viewer), and their running turn commits nothing
  there. agent-api now reads the identity registry on every access decision instead of keeping a
  copy for 2 seconds. See [Workspaces](/core/workspaces).
- **Old in-workspace policy files are read once.** The first agent-api start on this release copies
  every workspace's `policy/*.json` into the policy store and records that it did
  (`.policy/_tree_adoption.json`); no later read or start reads those files, so a roster planted in a
  workspace grants nothing. `VEXA_POLICY_LEGACY_READ` can stay unset. See
  [Workspaces](/minutes/workspaces).
- **An agent's page write is marked as the agent's.** Commits an agent makes through the page routes
  end with `On-behalf-of: <person>`, `Written-by: agent` and `Agent-unit: <unit>`; the person's own
  writes carry none. See [Pages](/minutes/pages).
- **Acts that reach other people need the person's press.** A watching agent's notice to someone
  else, its message into another person's conversation, a calendar event with other attendees or
  notifications, and a workspace publish now need the person's press of a card naming the target
  (`pressed`), else `403 press_required`, exactly as adding a chat participant already did. See
  [Shared chats](/minutes/shared-chats).
- **Rings are rate-limited.** One caller rings one person at most `VEXA_ROOM_RING_PER_PERSON` times
  (default 3) and anyone at most `VEXA_ROOM_RING_PER_CALLER` times (default 30) in 10 minutes;
  `call_person` answers `429 ring_rate_limited` with `Retry-After`. See [Rooms](/minutes/rooms).
- **Transcript signals: a wake by name needs a named speaker.** A line with no attributed speaker no
  longer wakes the agent by name, and being addressed past the hour's wake cap is bounded. See
  [Transcript signals](/transcript-signals).
- **A room's meeting belongs to the room.** The bot a room starts is billed to its payer as before,
  but the payer's own key or session no longer owns that meeting: they read it as a room member,
  cannot share, move or delete it, and stop reading it once they leave the room. See
  [Identity and trust](/architecture/identity-and-trust).
- **The workspace store keeps the generation it acknowledged.** A stale writer's claim left at the
  same generation is never elected over an acknowledged one (`gen/*.ack` objects in the bucket).
