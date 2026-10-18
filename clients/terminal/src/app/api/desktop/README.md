# terminal/src/app/api/desktop — the desktop as drawn (#1783, desktop ADR D1)

`GET /api/desktop` composes per request: crm-api `desk/read` (the person's arrangement), then agent-api
`desktop/sections` with the placed refs, then the pure `composeDesktop` (`../../../platform/desktop.ts`).
Nothing is stored. A crm refusal (feature off) is answered as given, and the page keeps the current home.
`[op]/` forwards the person's arranging verbs; `card/[op]/` the person's card verbs (D2, `desktop_cards`). `desk.ts` holds the shared half: the person's own key
(the session cookie, never a deployment key), the origin check and the one gateway call.
