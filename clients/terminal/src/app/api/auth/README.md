# auth

Terminal-owned authentication. The auth contract downstream is the httpOnly `vexa-token` +
`vexa-user-info` cookies (read by `server.mjs`'s WS proxy, `api/proxyAuth.ts` and `me/`). The info
cookie is display-only: nothing decides who somebody is from it.

Two doors, and no third:

- **OAuth** — `[...nextauth]/` brokers Google, Microsoft and a generic OIDC provider (ADFS,
  Keycloak; `oidcConfig.mjs`, `VEXA_OIDC_*`) via NextAuth. Its `signIn`
  callback runs the same find-or-create+mint flow as everything else (`adminApi.ts`) and sets the
  two cookies. Providers self-gate on env presence (`GOOGLE_CLIENT_*` / `MICROSOFT_CLIENT_*`,
  `NEXTAUTH_URL`, `NEXTAUTH_SECRET` — sourced from `vexa-secrets`). The UI (`AuthGate.tsx`)
  discovers enabled providers from NextAuth's `/api/auth/providers`.
- **Email magic link** — `request-link/` mails a signed, single-use link; `redeem/` verifies it and
  sets the cookies. Control of the mailbox is the proof of identity. `magicToken.ts` owns the token
  (HMAC-SHA256 over `{email, exp, jti}` with the link key, 15-minute default TTL capped at 60
  minutes, single use across every replica through admin-api's record, `POST /internal/signin-links/redeem`) and the `next=` open-redirect guard; `mailer.ts` is a dependency-free SMTP
  client driven by the deployment's mail family, `VEXA_MAIL_SMTP_HOST` / `_PORT` / `_FROM` (+ optional
  `_USER`/`_PASSWORD`, `_SECURE`, `_TLS_INSECURE`), declared in `clients/terminal/config.v1.json`.

**Who may sign in** (Vexa-ai/vexa#1783) is decided by admin-api alone; this process holds no list.
Every door admits only an existing user of this instance, an admin (the claimed one, or an address in
admin-api's `VEXA_ADMIN_EMAILS`), or an address on the sign-in allow-list — admin-api's
`VEXA_SIGNIN_ALLOW` plus the `signin.allow` setting the admin edits under Settings → Sign-in — and,
while no admin has been claimed and neither list is configured, the sign-in that claims the
instance. Who may claim is admin-api's answer too. `signinAdmission` in `adminApi.ts` asks admin-api
(`POST /internal/signin-admission`) and **fails closed**: no answer, a 404 from an older admin-api, or
anything but a literal `admitted: true` refuses. `findOrCreateUserToken` asks before it can create an
account, so OAuth and redeem (including a test user's link) cannot skip it; `request-link/` asks before it mails. A
refused person sees one sentence (`../../signinRefusal.ts`) at the redeem page and on the sign-in card
(`?error=` after OAuth); the email form answers "check your email" either way.

**Test users** (`test-users/`, `testUsers.ts` — app.dev tooling, #1783). With `VEXA_DEV_TEST_USERS=1`
(compose only; Helm and Lite never set it), an instance administrator's account menu offers *Test
users…*: list and create `tester-<name>@example.test` accounts (admin-api's ordinary create) and mint a
one-time sign-in link for one (the emailed door's token, 15 minutes, single use), redeemed through
`redeem/` and admission like any other. Refused unless switched on (404), unless the caller is an
administrator (401/403), and for any address off `example.test` (400). Each mint is logged with who
asked for which address.

**The signing secret** (`authSecret.mjs`, one file read by both `server.mjs` and the routes).
`NEXTAUTH_SECRET` must be at least 32 bytes and not a value published in this repository;
`server.mjs` refuses to start otherwise. The emailed link is never signed with it directly: the link
key is `MAGIC_LINK_SECRET` when configured (held to the same rule, and different from
`NEXTAUTH_SECRET`), else HMAC-SHA256 of `NEXTAUTH_SECRET` under a fixed label. With no usable secret
nothing is minted or verified.

One link is both door and destination: `/api/auth/redeem?t=<token>&next=<relative-path>` carries
the deeplink the visitor was reaching for (`?ask=`, `?meeting=`, `?view=`), so a click lands them
authenticated and where they meant to be, in one hop.

**Which doors exist** is `VEXA_SIGNIN_METHODS` (`oidcConfig.mjs`): a subset of google, microsoft,
oidc, email. A door left out is not registered, or for the emailed link its routes answer 404, and
`instance/` says `email_link: false` so the card draws no form. `server.mjs` refuses to start on an
unknown method or a half-configured OIDC provider. Operator guide:
[`docs/docs/sign-in-oidc.mdx`](../../../../../../docs/docs/sign-in-oidc.mdx).

`login/` — direct email login — is **development-only** and answers 403 on any other build. To sign
in against a deployed container, request a link and redeem it. `logout/` clears the vexa cookies and the NextAuth session cookies. `company/` is the Company profile switch: an instance administrator works as the admin account on this tab and switches back (ADR-0049). `adminApi.ts` is the
server-only admin-api client.

The user-facing description, the end-to-end flow and the compliance notes are in
[`docs/docs/authentication.mdx`](../../../../../../docs/docs/authentication.mdx).
