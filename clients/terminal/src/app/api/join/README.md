# terminal/src/app/api/join

Server-side reads for the invite page at `/join`.

`preview/` is the one call on this terminal that reaches agent-api without a
user key, for the same reason agent-api's own
`GET /api/workspace/invites/preview` takes no subject: the card has to render
for a visitor who has no account here yet
([Vexa-ai/vexa#1635](https://github.com/Vexa-ai/vexa/issues/1635)). The token is
the capability; nothing here grants anything.

The **redeem** is not here — it is `POST /api/workspace/invites/accept` through
the ordinary authenticated proxy, where the gateway's verified email is what
enforces a bound invite.

`accept/` (PRD 0.13.4 S2) is the one click from an invite mail: `POST` only, from
the join page's own form (same-origin, counted per client address). It asks
agent-api to spend the sign-in half of a MAILED invite once
(`/internal/invites/signin-claim`), asks admin-api to admit the address
`via_invite`, finds or creates the account, accepts the invite as that person
through the gateway, and lands on the room or the workspace with the session
cookies. Every refusal is a 303 back to `/join?i=…&n=<why>`. `GET` and `HEAD`
never act, so a mail scanner opening any link changes nothing.
