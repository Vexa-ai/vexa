- **One key streams what it owns over `/ws` (#1784).** Any valid key opens `/ws` and subscribes to the
  meetings it may read (its own, a workspace's, a shared one); `/ws` takes no key scope, so a key that
  sends a bot also streams that bot's transcript. Ownership, workspace and share checks, and the live
  re-authorization, are the boundary.
