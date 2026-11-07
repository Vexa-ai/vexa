# agent · workspace-seeds

Seed workspaces planted into a fresh agent run. Each subdir is one named template; `seeding.resolve_seed_dir`
selects one by name (config `default_template`, default `default`), so adding a flavor is a new subdir —
no code change. A subdir is a valid seed iff it carries `CLAUDE.md` (`validate_seed`).

Every seed's `kg/` subtree is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
(OKF) v0.1 bundle — markdown entities with YAML frontmatter, reserved `index.md` listings per
directory. Our frontmatter contract (`workspaces/contracts/workspace.v1`, required `type`/`id`/`title`)
is a strict superset of OKF's (required `type` only), so seeded workspaces are portable to any OKF
consumer as-is.

- `default/` — the baseline seed checked out into a new agent workspace before the first turn.

A seed carries no routines. A routine is a person's own flow, made with the flows tools after the
workspace exists ([ADR-0046](../../docs/adr/0046-flows-absorb-routines-flows-own-time-flows-are-scoped.md));
a `routines/*.md` file in a seed does nothing.
