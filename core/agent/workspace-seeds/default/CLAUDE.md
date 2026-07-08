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

**Reference entities as `[[Title]]` everywhere — chat replies included.** The client renders
`[[wikilinks]]` as clickable entity chips and workspace file paths (backticked or as markdown
links) as clickable links, in chat and in docs alike. A plain-text mention of a known entity is
a dead end for the reader. Don't `[[link]]` things that have no entity doc — create the entity
first, or use plain text.

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

## Interface components (optional, render-rich)

The terminal renders entity bodies as MDX with a **closed component registry** — a doc can be an
*interface*, not just text. Everywhere else (git, plain editors) the tags degrade to readable
markup, so files stay portable. Unknown tags or malformed MDX fall back to plain-markdown
rendering; never invent tag names outside this list.

**The registry:**

- `<Note>…</Note>` / `<Warning>…</Warning>` — callouts. Note = context worth surfacing (source,
  freshness, caveat); Warning = risk or deadline the reader must not miss.
- `<Card title="…" icon="…" href="kg/…">one-line teaser</Card>`, grouped in
  `<CardGroup cols={2}>…</CardGroup>` — clickable navigation; an `href` to another workspace file
  opens it in-app, external URLs open in the browser. Icons: `user`, `building`, `cal`, `tasks`,
  `file`, `folder`, `link`, `zap`, `spark`, `web`, `git`.
- `<Steps><Step title="…">…</Step></Steps>` — anything sequential: plans, processes, timelines.
- `<Tabs><Tab title="…">…</Tab></Tabs>` — alternative views of the same subject
  (e.g. Background / History, Agenda / Decisions).

**Patterns that work well:**

- `[[wikilinks]]` stay the primary inline cross-reference — they render automatically as entity
  chips, colored by type (person/company/organization/project), no tags needed.
- End a substantial entity doc with a `## Connected` section: a `CardGroup` of its 3–5 most
  important related docs, each teaser saying *why* it's related (not "Referenced in this doc").
- Meeting docs: `<Tabs>` for Agenda / Decisions / Follow-ups; a `<Warning>` for anything with a
  deadline.
- Project/plan docs: `<Steps>` for phases with owners as `[[wikilinks]]` inside each step.
- `index.md` dashboards: a short `<Note>` saying what lives here + a `CardGroup` of the main
  sub-pages with counts.

Structure for the human scanning the doc; don't decorate for its own sake. A wall of components
is worse than clean prose — one Note, one CardGroup, one Steps per doc is usually the ceiling.

**MDX gotchas (these trigger the plain-markdown fallback for the WHOLE doc):**

- A bare `<` followed by a letter starts a tag: write `a < b`, `5<10` with spaces, or backtick it.
- Bare `{…}` is treated as an expression: backtick literal braces (`` `{"json": "like this"}` ``).
- Every tag must close (`<Note>…</Note>`, self-close `<Card … />` only when it has no children).
- Component tags need a blank line before and after them to keep the markdown inside them parsing.
- The fallback is safe (the doc still renders as plain markdown) but loses all interactivity —
  after writing components, re-read your output for these four mistakes.

## The README is this workspace's dashboard

`README.md` is not documentation — it is the workspace's **living dashboard**: an at-a-glance view of
the few things that matter here, kept current as the workspace grows. This is the **essence of every
workspace** — its README should always answer "what's in here and what matters right now." Keep it
short and scannable; update it as a side effect of the work (new key person/company/meeting/task →
reflect it in the README), not as a separate chore. It is the pinned view the user lands on.

## Who the user is (identity)

- **Where identity lives.** A **light reference** — at minimum the user's **name** — lives in the
  always-mounted PRIVATE SYSTEM tier at `_system/identity.md`, so you know who you're helping on every
  turn even when this workspace is switched off. If that name is still unknown, **ask the user their
  name early** and record it there.
- **The full profile lives HERE.** This workspace holds the user's complete profile as exactly one
  `person` entity marked `self: true` under `kg/entities/person/` (company, role, location, LinkedIn,
  relationships, history). Keep `self` unique — never set it on a second person. Link the `_system`
  light reference to this node once it exists.

## How to work

- To record a person/company/meeting/etc., create or update its entity file under `kg/entities/`.
- For recurring or scheduled work, use the **scheduling** skill.
- Keep facts dated and attributed where it helps. Do not invent — only record what you were given or
  found.
- You do **not** run git — commits and history happen outside your turn. Just write the files.
- Confirm briefly in your reply what you wrote (e.g. "Created `[[Jane Liu]]`").
