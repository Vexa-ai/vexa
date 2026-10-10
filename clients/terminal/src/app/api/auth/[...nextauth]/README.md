# [...nextauth]

NextAuth catch-all route — the OAuth broker for Google + Microsoft sign-in. NextAuth owns only the OAuth
handshake; the `signIn` callback converts the verified identity into the terminal's `vexa-token` +
`vexa-user-info` cookies via `findOrCreateUserToken` (`../adminApi.ts`), so the rest of the terminal's
auth is unchanged. That function asks admin-api whether the address may sign in at all
(Vexa-ai/vexa#1783) before it creates anything; a refusal sends the browser back to
`/?error=SigninNotAllowed` (or `SigninUnavailable`, fail closed), which the sign-in card turns into one
sentence. Before that, the callback takes the address only from a provider that verified it
(`../providerIdentity.ts`): Google's `email_verified === true`; for Microsoft, the ID token's `tid`
must be the tenant `MICROSOFT_TENANT_ID` pins, or — on a multi-tenant authority such as `common` — the
token must carry `xms_edov: true`. Otherwise the browser lands on `/?error=SigninUnverified`.

Known gap: accounts are still found by their (verified) email alone. Binding an account to the
provider subject (`google:<sub>`, `microsoft:<tid>:<oid>`, which `providerIdentity.ts` already
returns) needs an admin-api route that stores it on the user; there is none yet.

Providers self-gate on env presence; credentials come from `vexa-secrets` (see the
parent `README.md` and the repo `.env.local`). Mirrors the production webapp's nextauth route.
