- **A bot requested inside a workspace is the workspace's meeting, live for every member (#1648).**
  `POST /bots` takes an optional `workspace_id`, authorized against the caller's own resolved
  memberships, and writes it to `data.workspace_id` at spawn; the chat's `bot_send` / `bot_schedule`
  send the workspace the conversation is working in, so *"assign it to the group"* binds instead of
  being refused. Every member of the bound workspace then sees the meeting while it runs — in the
  list (already), on the meeting page, in the live transcript, in `/api/meeting/note` and
  `/api/meeting/terms`, and in the bot's status, which is now published to a per-workspace channel
  each member's socket joins at connect. A workspace's front page lists its meetings, live ones
  first, and the write-up mail reaches the workspace's members rather than the requester alone.
  Unbound meetings are untouched: no bind, no widening, no extra mail.
