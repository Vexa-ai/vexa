# Keycloak SSO overlay

Optional OIDC sign-in for the terminal. Vexa's declared identity direction is
[SPIRE + Keycloak + RFC 8693](../../../docs/docs/architecture/identity-and-trust.mdx); this is its
first landing: **users sign in to the terminal with Keycloak instead of an emailed/typed key.**

It is **additive**. Without the overlay nothing changes: the terminal's Keycloak provider self-gates
on `KEYCLOAK_CLIENT_ID` + `KEYCLOAK_CLIENT_SECRET` + `KEYCLOAK_ISSUER`, exactly like the Google and
Microsoft buttons. Existing API keys and the dev email login keep working either way — Keycloak is
never a single point of lockout.

## Run it

```bash
cd deploy/compose
docker compose -p vexa-v012 -f docker-compose.yml -f docker-compose.keycloak.yml up -d
```

Then open the terminal and press **Continue with Keycloak**. The bundled realm ships one dev user:
`demo` / `demo`. Keycloak's own admin console is on `http://localhost:18101` (`admin` / `admin`).

## How an identity becomes a Vexa user

Keycloak verifies the person and returns their email. The terminal then reuses the SAME
find-or-create+mint path the Google/Microsoft buttons use (`findOrCreateUserToken` in
`src/app/api/auth/adminApi.ts`), so a Keycloak login produces an ordinary Vexa user and API token.
Nothing downstream of sign-in learns about Keycloak — the core's contracts are untouched.

**The email is the account key.** That is why sign-in requires Keycloak to assert
`email_verified: true`. A realm that lets people self-register unverified addresses would otherwise
let someone claim another person's Vexa account by registering their address. The bundled realm sets
`registrationAllowed: false` as a second layer.

## Going to production

The overlay is shaped for production but ships local defaults. Change all of it:

| What | Dev default | Production |
|---|---|---|
| Client secret | `dev-terminal-secret` | Rotate in Keycloak; set `KEYCLOAK_CLIENT_SECRET`. Never commit it. |
| Admin account | `admin` / `admin` | A real account with a strong password |
| DB password | `keycloak` | A generated secret |
| `KEYCLOAK_PUBLIC_URL` | `http://localhost:18101` | Your public **https** origin |
| Redirect URIs | `localhost:3000` / `localhost:13000` | Your exact callback URLs — keep them exact, never `*` |
| Server mode | `start-dev` | `start` behind TLS (set `KC_PROXY_HEADERS=xforwarded` when proxied) |
| Users | bundled `demo` user | Your own users, or federate to an upstream IdP (LDAP, or your university/company SSO) |

Keycloak runs on its **own** postgres (`keycloak-db`), deliberately not the stack's: the identity
store stays blast-radius isolated from meeting data, and enabling or removing this overlay never
migrates the `vexa` database.

### The issuer trap

`KC_HOSTNAME` pins the `iss` claim Keycloak stamps into every token. If the browser reaches Keycloak
at one address and a server-side call reaches it at another, the issuer will not match what the token
carries and tokens get rejected as invalid — with no obvious error. Keep `KEYCLOAK_PUBLIC_URL`,
`KEYCLOAK_ISSUER` and the address users actually type in agreement.
