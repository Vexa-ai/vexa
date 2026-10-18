# terminal/src/app/join

`GET /join?i=<token>` — where a workspace invite is redeemed.

The founder minted an invite, opened the link and read *"not found"*
([Vexa-ai/vexa#1635](https://github.com/Vexa-ai/vexa/issues/1635)): the link
pointed at the MCP host, and nothing served `/join` here. The base is fixed
where the link is composed — agent-api builds it on the deployment's declared
public app URL (`VEXA_UI_URL`) — and this directory is the page.

The order is the design: **say what the invite is, then ask who they are.**
`preview` renders for somebody with no account (it is capability-gated by the
token, not by a session), then the instance's own sign-in carries
`next=/join?i=…`, then `POST /api/workspace/invites/accept` — the route that
already existed — then the workspace's front page at `/w/<id>`.

`joinState.ts` holds every decision as a pure function, including the closed
set of refusals: expired, spent, withdrawn, unknown and wrong-address each get
one sentence. **Never a 404** — that is what the founder got, and it says
nothing about the invite in the reader's hand.

Tests: `../__tests__/join.test.tsx`.
