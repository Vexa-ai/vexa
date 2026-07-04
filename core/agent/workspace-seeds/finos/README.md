# workspace-seed · finos

> **License & provenance.** Code/scaffolding here is **Apache-2.0** ([LICENSE](LICENSE)); the
> knowledge-graph content under `kg/` is also offered under **CC BY 4.0**. The graph was assembled
> entirely from **public FINOS sources** (every entity cites its `Source:`) — see [SOURCES.md](SOURCES.md)
> for provenance and the **privacy / personal-data** notice (the `kg/entities/person/` entries are
> professional public-capacity facts; removal requests honored at finostest@vexa.ai). Attribution and
> third-party notices: [NOTICE](NOTICE).

FINOS-flavored knowledge-worker seed, captured from the `finostest@vexa.ai` workspace. It is the
`default` seed plus a pre-populated knowledge graph of the FINOS ecosystem under `kg/entities/`
(companies, organizations, people, projects) and `routines/finos-discovery.md` — the every-30-min
job that walks the FINOS GitHub org to grow that graph. The discovery routine ships `enabled: true`;
set its frontmatter `enabled` to `false` to seed it dormant. (Everything else below is the shared
seed mechanics, identical to `default/`.)

The per-person workspace template a new chat/unit workspace is seeded from (`SubprocessChatRunner`
copies it, then `git init`s). `CLAUDE.md` carries the entity conventions the agent writes to —
`kg/entities/<type>/<slug>.md` with required frontmatter (`type`, `id`, `title`) plus type-specific
fields (e.g. a `task` carries `status`/`priority`/`due`/`source`). Every write is re-validated against
`workspace.v1` before commit, so these conventions are guidance; the contract is the gate.

`agents/`, `skills/`, and `routines/` are the per-workspace agent-extension homes — all VISIBLE,
non-dotfile, git-tracked. `agents/meeting.md` steers the live copilot; `skills/<name>/SKILL.md` are
Claude Code skills the worker auto-discovers (the governed `skills/` tree is symlinked into
`.claude/skills` per turn). `routines/<name>.md` files compile to durable Vexa Scheduler jobs for
recurring work, using frontmatter `enabled`, `cron`, and `prompt` plus optional body text.
`skills/scheduling/` is the discoverable skill that teaches the agent to author those routine files
(so the durable-scheduling capability is learned on demand rather than living in always-on `CLAUDE.md`);
`skills/hello-workspace/` is a minimal example — replace or delete it. Skill helper scripts run under
whatever `--allowedTools` the turn already grants (no separate skills gate).
