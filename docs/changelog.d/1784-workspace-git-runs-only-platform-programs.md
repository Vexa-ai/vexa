- **Workspace git runs only what the platform chose (#1784).** agent-api and the agent worker now run
  every git command with repository hooks, fsmonitor and every program a repository can name
  (filter, diff and merge drivers, credential helpers, signing programs, includes) switched off, and
  reduce a workspace repository's `.git/config` to plain settings (remotes, branch tracking,
  identity) before using it. The model's tools can still read a workspace's history but no longer
  write its `.git`; the platform records each turn's commit as before. Push and pull re-check a
  workspace's home URL the way an attached repository is checked; a self-hosted install that syncs
  with a repository on local disk opts its root in with `VEXA_ALLOW_LOCAL_REPO_ROOT`.
