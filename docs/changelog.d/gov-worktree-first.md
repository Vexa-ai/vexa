- **Governance: a session's own worktree comes before its first edit.** The actor contract
  (AGENTS.md) now spells out the two trespass tells — `git worktree add` answering "already
  used by worktree" and uncommitted files you didn't write — and the remedy for each: branch
  from the ref into a fresh worktree of your own, never edit `main`, the primary checkout, or
  another session's tree. D14b ties the claim lease to that own-worktree rule.
