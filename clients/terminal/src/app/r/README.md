# terminal/src/app/r

`/r/<kind>/<id>` — where a universal reference lands (#1783): a link to a workspace, chat, page,
entity, meeting, flow or sensor, pasted anywhere. The page asks agent-api (`GET /api/refs/resolve`)
what it is for this reader and goes there (`/w/…`, `/meetings/<id>`, the chat, `/join/<token>`), or
says one sentence and offers "Request access". `refLanding` in `refRoute.ts` is the decision (pure,
tested in `../__tests__/refRoute.test.ts`).
