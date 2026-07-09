# workspace.v1 — the agent's git workspace convention

A workspace is a **user-owned git repo** (the agent's durable memory) — *data, not platform code*. vexa
owns only two things, both light:

1. **The init template** — the repo layout a fresh workspace starts from:
   ```
   sg/                     strategy graph (free-form markdown)
   kg/entities/<type>/<slug>.md   typed entities, each with EntityFrontmatter
   ```
2. **The base `EntityFrontmatter` convention** (`$defs`) — `type · id · title` required, plus optional
   `created · updated · tags · aliases`. **Extensible**: customers add fields per entity type
   (`additionalProperties: true`) — the entity *schema* is config, not platform (P11).

The agent clones/commits this repo itself (via `env`, see `runtime.v1`); the kernel never sees "workspace".
Access/sharing + the tier model (`_global`/normal/`_system`) + live collaboration are **delivered** —
see [`docs/WORKSPACES.md`](../../../../docs/WORKSPACES.md) (ADR-0003 is the original data/sharing policy).
Goldens validated by `gate:schema`.
