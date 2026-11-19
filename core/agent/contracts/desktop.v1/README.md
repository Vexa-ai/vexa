# desktop.v1 — the desktop, as a read model — UNSEALED

The desktop (desktop D1, feature `desktop`, install scope, needs `crm`) is drawn from two owners and
stored by neither of them as a whole:

- **crm-api** stores what the person placed (`agent:`, `chat:` and `file:` refs) and how they arranged the
  sections (`crm.v1` `Desk`, `DeskLayout`, `DeskOp`).
- **agent-api** answers what those things ARE now, plus the one section nobody places: every
  workspace the person can open. That answer is `DesktopSections`, served by
  `POST /api/desktop/sections` (`core/agent/control_plane/routers/desktop.py`) to the person's own
  app only — a delegated caller is refused `person_only`.

The terminal's `GET /api/desktop` reads both per request and composes `Desktop`. Nothing is stored
for the composition, and no model is called anywhere on the way.

| Shape | What it is |
|---|---|
| `DesktopSectionsRequest` | `{refs}` — at most 100 placed refs. Kinds other than `agent:`, `chat:` and `file:` are not resolved here (`card:` refs are crm-api's: the desk read carries the cards themselves). |
| `DesktopSections` | `presence_visibility` as applied, the `workspaces` (hidden ones only counted in `hidden_workspaces`), the `agents` and the `files`, and `chats` when `chat:` refs were asked for. |
| `WorkspaceTile` | name (never a slug the person did not choose), kind, access and write, members (a private one: only the person; a shared one's roster to its contributors and owners; a reader sees none), last activity, last and live call — every meeting kind (Meet, Teams, Zoom, Jitsi bot meetings and Vexa calls) with its `platform`, among the meetings the viewer may read. |
| `AgentTile` | one of the person's OWN routines: what it does, status from flows' own state (no `paused` is stored for the desktop), last and next run, the workspace its chat works in, its chat. |
| `ChatTile` | one of the person's OWN chats: its title ("Untitled chat" when unnamed, never its id), when it was last active, the workspace it works in. Drawn in Agents beside the routines. |
| `FileTile` | a file the viewer can open, by the guard the file read uses: title, workspace, last change. |
| `Missing` | a ref that is not the person's own, is gone, or cannot be opened by this viewer. It says nothing else. |
| `Unavailable` | flows did not answer; the agent tile is drawn without a status rather than with a guessed one. |
| `Desktop` | the terminal's composition: sections in the person's order with their items, proposals, filters, saved arrangements. |
| `CardView` | a card as crm-api answers it to this viewer (D2, feature `desktop_cards`): title, what it is about, its stage on the type's lifecycle, next and waiting, sub-cards, members, the latest movement, `stalled`, what the viewer may do, and its face. `activity` only from `/card/read`. crm.v1 owns it; it is mirrored here because the composition carries it. |
| `FaceBlock` | a resolved face block (blocks.v1 face profile): structural blocks carry only their type, data blocks arrive filled, a field the viewer cannot read arrives as `locked`. The terminal sanitizes again. |
| `CardItem` | a card tile in the Cards section: a `CardView` with `kind: card`. |
| `CardProposal` | a pending card proposal for the person (Add / Not now), with who proposed it and why. |
| `AttentionRequest` · `CardIn` | the attention home's request (home R3, feature `home2`): the crm desk's cards as the terminal read them (they never widen access) and, for the workspace page, one `workspace`. |
| `Attention` · `AttentionWorkspace` · `Need` · `Act` · `Work` | what `POST /api/desktop/attention` answers: every need ranked, and per workspace (the same slugs as `DesktopSections`) its needs, what is working, rooms active, the live call and the calm line; one workspace adds `rooms`, `chats`, `routines` and `calls`. Each need's `act` points at the owner's route. |
| `RefsRequest` · `Refs` · `RefVisible` · `RefInvisible` | what `POST /api/desktop/refs` answers for a rich README's typed references (`room` `chat` `call` `file` `person` `card` `routine` `workspace`): what each is for this viewer, or exactly `{kind, visible: false}`. |

A meeting belongs to a workspace when its note page lives there (the row's recorded `note_path`, or a
page whose frontmatter names the row), its chat works there (the chat that sent the bot, the meeting's
own chat, a Vexa call's chat), or it is bound there (`data.workspace_id`). Only rows in the viewer's
own meetings list count, so a tile never reveals a meeting the viewer cannot read. The line is the
meeting's display name, never an id; Join opens a Vexa call's chat and a bot meeting itself.

Presence is coarse ("now" within 10 minutes, "today" within 24 hours), never the viewer's own, never
for a person who switched theirs off, and governed by `_global/POLICIES.md` `presence_visibility`
(`off` · `shared_chats` · `workspace_members`, default `workspace_members`; a value nobody can read
is applied as `off`).

Goldens: `golden/` (see its README). `gate:schema` runs `validate.mjs`; the route's own answers are
validated against this schema by `core/agent/tests/test_desktop_sections.py`.

**Cards (D2).** While `desktop_cards` is on, crm-api's desk read carries `cards`, `card_proposals` and
`card_invites`; the composition draws the Cards section from `cards` (in the layout's order), carries
the two pending lists and sets `cards_on`. Off, the read carries no `cards` key, the Cards section stays
hidden and the composition carries nothing card-related. `filters` is answered as a list of
`{section, where, until?}` (the object form stays accepted).
