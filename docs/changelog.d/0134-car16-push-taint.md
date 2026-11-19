- **A worker's push is held to the private-write rule on the server (s3 backend, M2).** agent-api
  checks every file version in a pushed commit range against the session's privacy mark and the
  workspace's roster at the push's base, the same check a page write gets. A session that read the
  person's private data can no longer get it into a workspace other people read by committing it
  itself. The push is refused with `409 private_sources_to_shared`, and the held paths and their
  confirmation codes are named. Pushed paths under `.claude/` are now refused too, and every
  reserved push path is compared in any letter case (M3). See [Workspaces](/core/workspaces).
