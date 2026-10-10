# auth/request-link

`POST {email, next?}` — mints a signed, single-use magic-link token and mails
`<base>/api/auth/redeem?t=<token>&next=<relative-path>`.

- Mails ONLY an address that may sign in — an existing user, an admin, or one on the sign-in
  allow-list (Vexa-ai/vexa#1783). Nothing is mailed to anybody else, nor to anybody while admin-api
  cannot answer (fail closed).
- Always answers `200 {ok:true}` for a well-formed address, whether or not it is known here, allowed
  here, or the mail went out (no user or allow-list enumeration; refusals and delivery failures are
  logged server-side). The admission question and the send run after the response
  (`../linkDelivery.ts`), so the answer takes the same time either way.
- `400` for a missing/malformed address — including any address longer than 254 characters, which
  is refused before the address pattern runs (`../emailAddress.ts`) — `503` when the instance has no usable signing secret
  (`../authSecret.mjs`) or no configured public URL, the same for every address.
- The link's origin is the configured public URL (`NEXTAUTH_URL`, else `TERMINAL_URL`; a plain
  absolute http(s) URL), never the request's `Host` or `X-Forwarded-*` headers.
- Rate limited per client address and per email address (`../linkRateLimit.ts`; defaults 20 and 5
  per 15 minutes, `MAGIC_LINK_RATE_PER_IP` / `MAGIC_LINK_RATE_PER_ADDRESS` /
  `MAGIC_LINK_RATE_WINDOW_SECONDS`). Past the client limit: `429` with `Retry-After`, before admin-api
  is asked. Past the address limit: the usual `200`, and nothing is sent. The client address is the
  one `server.mjs` stamps (`../clientAddress.mjs`): the TCP peer, or the rightmost
  `X-Forwarded-For` entry when the peer is a private address or named in `TERMINAL_TRUSTED_PROXIES`.
- `next` is reduced to a site-relative path (`safeNext`) BEFORE it is written into the mail.
- Creates nothing and mints no session — that happens at `../redeem`, after the recipient proves
  they hold the mailbox.

Token rules live in `../magicToken.ts`; SMTP wiring (the `VEXA_MAIL_SMTP_*` family — host, port, from,
optional user/password, secure, tls-insecure) in `../mailer.ts`.
