- **One key streams what it owns over `/ws` (#1784).** `/ws` applies the REST routes' scopes, and
  every route takes `bot` or `tx` (#1922), so a key that sends a bot also streams that bot's
  transcript; it subscribes to the meetings it may read (its own, a workspace's, a shared one).
  Ownership, workspace and share checks, and the live re-authorization, are the boundary. Only a
  `browser`-only key is answered `insufficient_scope` and closed with `4403`.
