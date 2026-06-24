# workspace-seed

The per-person workspace template a new chat/unit workspace is seeded from (`SubprocessChatRunner`
copies it, then `git init`s). `CLAUDE.md` carries the entity conventions the agent writes to —
`kg/entities/<type>/<slug>.md` with required frontmatter (`type`, `id`, `title`) plus type-specific
fields (e.g. a `task` carries `status`/`priority`/`due`/`source`). Every write is re-validated against
`workspace.v1` before commit, so these conventions are guidance; the contract is the gate.
