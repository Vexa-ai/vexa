# golden vectors — desktop.v1

Each `<Shape>.<case>.json` validates against `#/$defs/<Shape>` via `../validate.mjs`:
`DesktopSectionsRequest.placed` (the refs the terminal sends), `DesktopSections.member` (a contributor
of a shared workspace with a live Vexa call and a last Zoom meeting, a live Google Meet bot meeting
on the person's own desk, presence by workspace activity, one placed agent, one gone,
one file) and `DesktopSections.off` (presence off, flows not answering, a file the viewer cannot
open), `Desktop.default` (the terminal's composition of crm-api's desk with the sections), and for D2 `Desktop.cards`
(the composition with `desktop_cards` on: a card tile, a proposal, an invite, a filter), `CardView.member`
(a member's card with a locked field on its face and its activity) and `CardProposal.create` (an agent's proposed card).
For home R3 (feature `home2`): `AttentionRequest.cards` and `Attention.member` (needs of every kind
across the desk and a shared workspace, a live call and an active room working there),
`Attention.workspace` (the one-workspace page's lists), `Attention.foreign` (a workspace the viewer
cannot open: empty), `RefsRequest.readme` and `Refs.mixed` (visible refs of each kind, and the one
invisible shape). `tests/test_desktop_sections.py` validates every answer the route gives against the same schema.
