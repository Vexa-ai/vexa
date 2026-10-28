- **`workspace_status`: an agent can look at a workspace's GitHub repository without changing it (#1783).**
  The new tool (`POST /api/workspace/status`) fetches the branch into its remote-tracking ref only and
  reports the branch, upstream, ahead/behind counts, the commits on each side (sha, subject, number of
  files), uncommitted and untracked paths, and when it fetched. The working tree, the index and the
  local branch are not changed. Any member may call it, and it is allowed with no person watching.
  Refusals are typed: `no_remote`, `detached`, `no_credential` and `network`.
  The front page's **Sync** act and the local-regime guidance now ask the agent to call it before
  `workspace_pull` or `workspace_push`; the default workspace prompt says the agent never runs git.
  See [Workspaces](/minutes/workspaces).
