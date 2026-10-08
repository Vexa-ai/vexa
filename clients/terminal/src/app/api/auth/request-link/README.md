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
- `400` for a missing/malformed address, `503` when the instance has no `NEXTAUTH_SECRET` and
  therefore cannot sign anything.
- `next` is reduced to a site-relative path (`safeNext`) BEFORE it is written into the mail.
- Creates nothing and mints no session — that happens at `../redeem`, after the recipient proves
  they hold the mailbox.

Token rules live in `../magicToken.ts`; SMTP wiring (`SMTP_HOST`/`SMTP_PORT`/`SMTP_FROM`, optional
`SMTP_USER`/`SMTP_PASS`/`SMTP_SECURE`) in `../mailer.ts`.
