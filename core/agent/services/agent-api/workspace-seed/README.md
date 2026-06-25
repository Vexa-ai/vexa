# workspace-seed

The per-person workspace template a new chat/unit workspace is seeded from (`SubprocessChatRunner`
copies it, then `git init`s). `CLAUDE.md` carries the entity conventions the agent writes to —
`kg/entities/<type>/<slug>.md` with required frontmatter (`type`, `id`, `title`) plus type-specific
fields (e.g. a `task` carries `status`/`priority`/`due`/`source`). Every write is re-validated against
`workspace.v1` before commit, so these conventions are guidance; the contract is the gate.

`agents/` and `skills/` are the two per-workspace agent-extension homes — both VISIBLE, non-dotfile,
git-tracked. `agents/meeting.md` steers the live copilot; `skills/<name>/SKILL.md` are Claude Code
skills the worker auto-discovers (the governed `skills/` tree is symlinked into `.claude/skills` per
turn). `skills/hello-workspace/` is a minimal example — replace or delete it. Skill helper scripts run
under whatever `--allowedTools` the turn already grants (no separate skills gate).
