# home-card.v1 — the new-chat home, as primitives — UNSEALED

What the background `home-compose` flow writes per person and the terminal draws on a new chat:
a greeting, up to seven ranked cards, and a grey line naming what was left out. A card is a small
tree of primitives — `title`, `text`, `person`, `row`, `list`, `evidence`, `checklist`, `action` —
and two layouts, `group` and `attach` (a card inside a card). The six kinds (`meeting`, `waiting`,
`suggestion`, `try`, `result`, `continue`) are labels for ranking and feedback, not components.

No HTML and no markdown: every string is plain text. An action sends a prompt to the agent, opens a
chat or a page by id, or opens a message to a person — nothing else. Both consumers (agent-api's
`services/home/spec.py` before it stores a composition, the terminal's `minutes/homeSpec.ts` before
it draws) drop unknown blocks and props and cap text at `x-limits`, so a malformed card costs that
card, never the page. `Feedback` is the body of `POST /api/home/feedback`.

Goldens: the approved mockup's three moments as `HomeDoc.{busy,after,quiet}.json`.
