# /api/sessions

Only the routes under `sessions/` that cannot go through the `[...path]` catch-all live here.

- `[session]/attachment/` — the bytes of a file attached to a chat message (a bubble's thumbnail,
  its lightbox). The catch-all reads answers as text and labels them JSON, which corrupts an image, so
  this route streams the bytes through with agent-api's own headers. Access is agent-api's decision:
  the conversation's participants, never a workspace's members.
