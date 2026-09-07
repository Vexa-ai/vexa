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
