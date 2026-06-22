# @vexa/dash-auth — the identity/session core behind a PORT

_dashboard_new/ · brick · the dashboard's session: token resolution + the login/logout flows._

The dashboard holds **one session object**, `AuthSession`, and reads identity through it — never
through `fetch`, `jsonwebtoken`, or `next-auth` directly. This brick **owns the session/token logic**
(token resolution + the login/logout flows) and pushes the heavy provider wiring out behind a thin
`AuthBackend` port.

- **`createAuthSession({ backend, selfHostToken? })`** — the session core. Returns `getToken()`,
  `getUser()`, `isAuthenticated()`, `loginSelfHost()`, `loginMagicLink(token)`,
  `loginOAuth(provider, code)`, `logout()`. It composes the backend primitives into the flows; it does
  no network/jwt/OAuth itself.
- **`createFakeAuthBackend(seed?)`** — an in-memory `AuthBackend` (users + minted tokens in maps; no
  network). `verifyMagicLink` decodes a `magic:<email>` token, `exchangeOAuth` decodes a
  `<provider>:<email>` code, and find-or-create + token minting are modelled in memory. Used to
  build/test the session without a live admin API / SMTP / OAuth provider.

The behaviour is grounded on the vendored dashboard auth surface
(`clients/dashboard/src/lib/{auth-cookies,vexa-admin-api}.ts` + `app/api/auth/{verify,send-magic-link,
oauth-callback,[...nextauth]}`): every login flow is **find-or-create then mint a token**
(`findUserByEmail` → `createUser` on miss → `createUserToken`); the email-source step (jwt verify vs
OAuth code exchange) is what differs, and that is delegated to the port. The clean version keeps that
core here and leaves the admin-API/JWT/OAuth round-trips for a real `AuthBackend` adapter.

## Token resolution

Every getter obeys one rule:

```
explicit session token   →   else selfHostToken   →   else null
```

- A successful `login*` sets the **explicit session token** (and the session user). It **wins** over
  any configured `selfHostToken`.
- With no login and a configured `selfHostToken`, the token IS the identity (the self-hosted single-key
  deploy): `isAuthenticated()` is true, `getUser()` is null (self-host carries no per-user record).
- `logout()` clears the explicit session token + user. Afterwards `getToken()` falls back to
  `selfHostToken`, or `null` when none is configured.

## Surface

Front door: [`src/index.ts`](src/index.ts).

The port (`AuthBackend`) — the provider primitives the session composes:

| method | role | returns |
| --- | --- | --- |
| `findUserByEmail(email)` | find half of find-or-create | `User \| null` |
| `createUser(email)` | create half of find-or-create | `User` |
| `createToken(userId)` | mint a session/API token | `string` |
| `verifyMagicLink(token)` | magic-link → verified email | `{ email }` |
| `exchangeOAuth(provider, code)` | OAuth code → verified email | `{ email }` |

The session (`AuthSession`) returned by `createAuthSession`:

| method | flow |
| --- | --- |
| `getToken()` | session token → selfHostToken → null |
| `getUser()` | the logged-in `User`, or null |
| `isAuthenticated()` | `getToken() !== null` |
| `loginSelfHost()` | resolve the configured token (no per-user step) |
| `loginMagicLink(token)` | `verifyMagicLink` → email → find-or-create → mint → set session |
| `loginOAuth(provider, code)` | `exchangeOAuth` → email → find-or-create → mint → set session |
| `logout()` | clear session token + user |

Also exported: `createFakeAuthBackend` (+ `FakeAuthSeed`) and the port types `AuthBackend`,
`AuthSession`, `CreateAuthSessionOptions`, `OAuthProvider`, `User`.

`User` mirrors the admin-API `VexaUserData` floor the vendored dashboard carries (`id`, `email`,
`name`, `max_concurrent_bots`, `created_at`); it is additive (extra keys allowed). It is an auth-owned
shape — `@vexa/dash-contracts` models the REST/WS surface, not the user record — so it lives here.

## Verify

`npm run build` — `tsc` clean. `npm test` runs [`src/auth.test.ts`](src/auth.test.ts) via `tsx` (exit
code is the signal): self-host resolves `getToken()` to the `selfHostToken` and is authenticated with
no login; `loginMagicLink(valid)` over the fake backend sets a freshly minted session token + the user
(and wins over a configured `selfHostToken`); `loginOAuth` does find-or-create (existing email reuses
the user, unseen email provisions one); invalid magic-link / mismatched OAuth code throw; and `logout`
clears to `null` (or falls back to `selfHostToken` when one is configured).

```bash
cd clients/dashboard_new/modules/dash-auth
npm i --no-audit --no-fund
npx tsx src/auth.test.ts
```
