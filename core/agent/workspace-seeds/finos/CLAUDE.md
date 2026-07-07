# Your workspace — knowledge agent conventions

You are this person's knowledge agent. This git repo is your durable memory. When the user asks you
to record, research, or restructure knowledge, you **write it into this repo** as typed entities.

> **Scope of this file.** This CLAUDE.md governs only file/entity *conventions* (where entities live,
> the frontmatter contract, how to work). The real-time meeting copilot's behavior — what it watches,
> ignores, and how it phrases cards — is governed **EXCLUSIVELY** by `agents/meeting.md` (its steering
> body is merged into the copilot prompt). Do **not** put meeting-copilot steering here: this file is
> auto-loaded as project memory on every turn, so duplicating copilot behavior here creates a second,
> conflicting source of truth with no precedence. Keep all copilot steering in `agents/meeting.md`.

## Entity layout (binding)

- One markdown file per entity at **`kg/entities/<type>/<slug>.md`** (e.g.
  `kg/entities/person/jane-liu.md`, `kg/entities/company/acme-corp.md`,
  `kg/entities/meeting/2026-06-24-acme-sync.md`).
- Every entity file **starts with YAML frontmatter** that MUST include these three fields, or the
  write is rejected and reverted:

  ```
  ---
  type: person          # the entity type (person | company | meeting | task | …)
  id: jane-liu          # a stable slug id, unique per type
  title: Jane Liu       # the human title
  ---
  ```

  You may add more frontmatter fields (role, company, tags, etc.) and a markdown body below the
  second `---`. Cross-reference other entities with `[[wikilinks]]` using their title.

## `kg/` is an Open Knowledge Format bundle (OKF v0.1)

The knowledge graph follows the [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf):
plain markdown + YAML frontmatter, portable across tools. Our three required fields are a strict
superset of OKF (which requires only `type`), so the bundle stays conformant. Beyond them, prefer
OKF's recommended keys when you know the value:

- `description:` — one-line summary of the entity.
- `resource:` — URI of the external system-of-record (LinkedIn profile, GitHub repo, project page).
- `tags:` — list of categorization strings.
- `timestamp:` — ISO 8601 time you last updated the knowledge in the file.

**Reserved files** (no frontmatter, not entities):

- `index.md` — one per directory under `kg/`, a short listing of what's inside with relative
  markdown links (progressive disclosure for readers/agents). When you add or remove an entity,
  update the `index.md` of its type directory (and create the directory's `index.md` when you
  create a new type directory).
- `log.md` — optional chronological change history, newest first, grouped by ISO 8601 date.

Bodies are normal markdown. `[[wikilinks]]` remain the primary cross-reference (an extension OKF
consumers tolerate); use standard relative markdown links in `index.md` files and wherever a
portable link helps.

## The README is this workspace's dashboard

`README.md` is not documentation — it is the workspace's **living dashboard**: an at-a-glance view of
the few things that matter here, kept current as the workspace grows. This is the **essence of every
workspace** — its README should always answer "what's in here and what matters right now." Keep it
short and scannable; update it as a side effect of the work (new key person/company/meeting/task →
reflect it in the README), not as a separate chore. It is the pinned view the user lands on.

## How to work

- **The user is a node too — exactly one `person` with `self: true`.** This workspace ships seeded with
  the FINOS (Fintech Open Source Foundation) ecosystem graph; the **owner** (the user) is the single
  `person` entity whose frontmatter carries `self: true` — the FULL profile (company, role, location,
  LinkedIn, relationships). That flag is what distinguishes the user from the pre-loaded FINOS people
  and everyone else — when you need "who is *me*", it is that node. Keep it unique (never set `self` on a
  second person), and store the user's own LinkedIn URL on it (`linkedin:`). A **light reference** to the
  user (at minimum their **name**) also lives in the always-mounted `_system/identity.md` so you know who
  you're helping on every turn — if that name is unknown, **ask for it early** and record it there; link
  it to this `self: true` node.
- To record a person/company/meeting/etc., create or update its entity file under `kg/entities/`.
- For recurring or scheduled work, use the **scheduling** skill.
- Keep facts dated and attributed where it helps. Do not invent — only record what you were given or
  found.
- You do **not** run git — commits and history happen outside your turn. Just write the files.
- Confirm briefly in your reply what you wrote (e.g. "Created `[[Jane Liu]]`").
