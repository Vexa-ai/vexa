# auth

Terminal-owned authentication. The auth contract downstream is the httpOnly `vexa-token` +
`vexa-user-info` cookies (read by `server.mjs`'s WS proxy, `api/proxyAuth.ts`, and `me/`).

- **OAuth (primary)** — `[...nextauth]/` brokers Google + Microsoft sign-in via NextAuth. Its `signIn`
  callback runs the same find-or-create+mint flow as the email path (`adminApi.ts`) and sets the two
  cookies. Providers self-gate on env presence (`GOOGLE_CLIENT_*` / `MICROSOFT_CLIENT_*`,
  `NEXTAUTH_URL`, `NEXTAUTH_SECRET` — sourced from `vexa-secrets`). The UI (`AuthGate.tsx`) discovers
  enabled providers from NextAuth's `/api/auth/providers`.
- **Direct email login (`login/`) — DEBUG only.** Restricted to addresses containing `test`; no SMTP,
  no password. `logout/` clears the vexa cookies and the NextAuth session cookie. `adminApi.ts` is the
  server-only admin-api client.
