# auth/request-link

`POST {email, next?}` — mints a signed, single-use magic-link token and mails
`<base>/api/auth/redeem?t=<token>&next=<relative-path>`.

- Always answers `200 {ok:true}` for a well-formed address, whether or not it is known here and
  whether or not the mail went out (no user enumeration; delivery failures are logged server-side).
- `400` for a missing/malformed address, `503` when the instance has no `NEXTAUTH_SECRET` and
  therefore cannot sign anything.
- `next` is reduced to a site-relative path (`safeNext`) BEFORE it is written into the mail.
- Creates nothing and mints no session — that happens at `../redeem`, after the recipient proves
  they hold the mailbox.

Token rules live in `../magicToken.ts`; SMTP wiring (`SMTP_HOST`/`SMTP_PORT`/`SMTP_FROM`, optional
`SMTP_USER`/`SMTP_PASS`/`SMTP_SECURE`) in `../mailer.ts`.
