# auth

Terminal-owned authentication. The auth contract downstream is the httpOnly `vexa-token` +
`vexa-user-info` cookies (read by `server.mjs`'s WS proxy, `api/proxyAuth.ts`, `me/`, and the
`api/minutes/*` seams, which read the user `id` out of the info cookie).

Two doors, and no third:

- **OAuth** — `[...nextauth]/` brokers Google + Microsoft sign-in via NextAuth. Its `signIn`
  callback runs the same find-or-create+mint flow as everything else (`adminApi.ts`) and sets the
  two cookies. Providers self-gate on env presence (`GOOGLE_CLIENT_*` / `MICROSOFT_CLIENT_*`,
  `NEXTAUTH_URL`, `NEXTAUTH_SECRET` — sourced from `vexa-secrets`). The UI (`AuthGate.tsx`)
  discovers enabled providers from NextAuth's `/api/auth/providers`.
- **Email magic link** — `request-link/` mails a signed, single-use link; `redeem/` verifies it and
  sets the cookies. Control of the mailbox is the proof of identity. `magicToken.ts` owns the token
  (HMAC-SHA256 over `{email, exp, jti}` with `NEXTAUTH_SECRET`, 15-minute default TTL, in-process
  single-use ledger) and the `next=` open-redirect guard; `mailer.ts` is a dependency-free SMTP
  client driven by `SMTP_HOST` / `SMTP_PORT` / `SMTP_FROM` (+ optional `SMTP_USER`/`SMTP_PASS`,
  `SMTP_SECURE`).

One link is both door and destination: `/api/auth/redeem?t=<token>&next=<relative-path>` carries
the deeplink the visitor was reaching for (`?ask=`, `?meeting=`, `?view=`), so a click lands them
authenticated and where they meant to be, in one hop.

`login/` — direct email login — is **development-only** and answers 403 on any other build. It was
previously reachable in production for any address containing `test` (and, in minutes mode, for any
address at all): a password-less bypass. To sign in against a deployed container, request a link and
redeem it. `logout/` clears the vexa cookies and the NextAuth session cookies. `adminApi.ts` is the
server-only admin-api client.
