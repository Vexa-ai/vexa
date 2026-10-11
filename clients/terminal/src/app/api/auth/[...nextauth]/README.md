# [...nextauth]

NextAuth catch-all route — the OAuth broker for Google, Microsoft and generic OIDC (ADFS, Keycloak) sign-in. NextAuth owns only the OAuth
handshake; the `signIn` callback converts the verified identity into the terminal's `vexa-token` +
`vexa-user-info` cookies via `findOrCreateUserToken` (`../adminApi.ts`), so the rest of the terminal's
auth is unchanged. That function asks admin-api whether the address may sign in at all
(Vexa-ai/vexa#1783) before it creates anything; a refusal sends the browser back to
`/?error=SigninNotAllowed` (or `SigninUnavailable`, fail closed), which the sign-in card turns into one
sentence. Before that, the callback takes the address only from a provider that verified it
(`../providerIdentity.ts`): Google's `email_verified === true`; for Microsoft, the ID token's `tid`
must be the tenant `MICROSOFT_TENANT_ID` pins, or — on a multi-tenant authority such as `common` — the
token must carry `xms_edov: true`; for the generic OIDC provider (`oidc`), the verified ID token must
come from `VEXA_OIDC_ISSUER` and carry `sub` and an address in `VEXA_OIDC_EMAIL_CLAIM`. Otherwise the
browser lands on `/?error=SigninUnverified`.

The account is then bound to the provider subject (`google:<sub>`, `microsoft:<tid>:<oid>`,
`oidc:<sha256(iss, sub)>`) through admin-api's `PUT /internal/users/{id}/provider-subject`; another
subject for an already-bound provider is refused.

The OIDC provider (`authOptions.ts` `oidcProviders`) uses discovery, PKCE + state + nonce, reads its
profile from the ID token only (never userinfo), and adds `VEXA_OIDC_CA_FILE` to the public roots.
Its redirect URI is `<public URL>/api/auth/callback/oidc`. Guide:
[`docs/docs/sign-in-oidc.mdx`](../../../../../../../docs/docs/sign-in-oidc.mdx).

Providers self-gate on env presence; credentials come from `vexa-secrets` (see the
parent `README.md` and the repo `.env.local`). Mirrors the production webapp's nextauth route.
