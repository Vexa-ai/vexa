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
`next=/join?i=…`, then **a Join click** on a card that shows the workspace, the
role and who sent it, then `POST /api/workspace/invites/accept` — the route that
already existed — then the workspace's front page at `/w/<id>`. A visitor who is
already signed in also gets the card and the click: opening a link never joins
anyone by itself, and joining never changes where their chats write (PRD 0.13.4
D26, security review H4).

**One click from the invite mail (PRD 0.13.4 S2).** When the preview says the
link signs in (`signin`: an invite this deployment mailed to its one address),
the page offers one **Join** button — a plain form POST to `/api/join/accept` —
that signs the person in and lands them in the room the invite named. When it
comes back with `n=…` the page says why and offers the ordinary sign-in. On a
phone the card fills the width inside a 16px gutter, nothing scrolls sideways,
the act is full width and 44px tall, and **‹ Back** sits at the top.

`joinState.ts` holds every decision as a pure function, including the closed
set of refusals: expired, spent, withdrawn, unknown and wrong-address each get
one sentence. **Never a 404** — that is what the founder got, and it says
nothing about the invite in the reader's hand.

A visitor who is already signed in confirms before joining (D26), and the card
says who they are signed in as, with **"Not you? Use another address"** (D35:
one person may sign in with several addresses). It signs the browser out and
shows the sign-in door again on the same invite; nothing joined. The same
button rides the wrong-address refusal, since the other address may be the one
the invite named. Its styles are in `join.css` (a 44px touch target).

Tests: `../__tests__/join.test.tsx`.
