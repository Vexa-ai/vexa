# [...nextauth]

NextAuth catch-all route — the OAuth broker for Google + Microsoft sign-in. NextAuth owns only the OAuth
handshake; the `signIn` callback converts the verified identity into the terminal's `vexa-token` +
`vexa-user-info` cookies via `findOrCreateUserToken` (`../adminApi.ts`), so the rest of the terminal's
auth is unchanged. Providers self-gate on env presence; credentials come from `vexa-secrets` (see the
parent `README.md` and the repo `.env.local`). Mirrors the production webapp's nextauth route.
