- **Workspace git runs only what the platform chose (#1784).** agent-api and the agent worker now run
  every git command with repository hooks, fsmonitor and every program a repository can name
  (filter, diff and merge drivers, credential helpers, signing programs, includes) switched off, and
  reduce a workspace repository's `.git/config` to plain settings (remotes, branch tracking,
  identity) before using it; git then reads that same checked directory, even if `.git` is renamed
  afterwards. The model's tools can still read a workspace's history but no longer write its
  `.git`; the platform records each turn's commit as before. At a repository's top level the
  model's tools can create, edit and remove their own files and edit the platform's, but can no
  longer rename or remove an entry the platform wrote there. Push and pull re-check a
  workspace's home URL the way an attached repository is checked; a self-hosted install that syncs
  with a repository on local disk opts its root in with `VEXA_ALLOW_LOCAL_REPO_ROOT`.
