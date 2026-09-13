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

## Many groups, one deployment — Organizations

`organizationsEnabled` is on. Keycloak 26's Organizations feature lets ONE realm serve many tenants,
each with **its own identity provider and email domains**, and routes users automatically: someone
typing `alice@acme.com` is sent to Acme's own SSO without picking anything from a list.

That is deliberately generic. Vexa is not tailored to one institution, so no organization's login is
baked into this realm — a deployment adds the organizations it actually serves (*Organizations* in the
admin console, or the `/admin/realms/vexa/organizations` API), and everyone else falls back to the
realm's own sign-in below.

## Keeping sign-up short

The registration form asks for **email and password only**. Keycloak's stock form also demands first
and last name; those are removed from the realm's declarative user profile, which is what the form is
generated from. Combined with `registrationEmailAsUsername`, the email doubles as the username, so
there is no separate username field either. Nothing downstream needs the names — the terminal derives
a display name from the address when they are absent.

**Forgot-password is not yours to build.** `resetPasswordAllowed` plus the SMTP settings mean Keycloak
owns the whole reset flow — the link, the expiry, the form, the re-login. There is no reset UI to write
or maintain in Vexa.

## Registration, email verification and 2FA

Self-registration is **on**, and the realm is configured so it is safe:

| Setting | Value | Why |
|---|---|---|
| `registrationAllowed` | true | visitors can create an account |
| `verifyEmail` | true | the address must be proven before the account works |
| `registrationEmailAsUsername` | true | one identity, keyed by the address Vexa also keys on |
| `CONFIGURE_TOTP` (default action) | on | every NEW user sets up an authenticator app at first login |
| `bruteForceProtected` | true | lock out password guessing |

`verifyEmail` and `registrationAllowed` **must move together**. Terminal sign-in refuses an identity
whose `email_verified` is not `true`, so registration WITHOUT verification would let people sign up
and then be silently rejected at Vexa's door — and, worse, would be the exact hole that lets someone
register a colleague's address to reach their Vexa account.

`mailpit` is a mail **catcher**: Keycloak really sends, but the message is trapped in a local web
inbox at <http://localhost:18025> instead of reaching anyone. Register a user, open that inbox, click
the verification link. Nothing is delivered to real addresses.

TOTP is a second factor at login; it is *not* a substitute for email verification. Only the address
proof tells us the person owns the identity Vexa keys accounts by. The bundled `demo` user is
pre-verified and carries no required actions, so it stays a one-click login for testing.

### ⚠️ Open registration meets first-run admin

On an instance with no admin yet, **the first successful sign-in claims the admin role**
(`instanceHasAdmin` / `findOrCreateUserToken` in `clients/terminal/src/app/api/auth/adminApi.ts`).
A Keycloak login also auto-creates the matching Vexa user, so with registration open the chain is:
*stranger registers → signs in first → owns your instance.*

Set **`VEXA_ADMIN_EMAILS`** before exposing an instance. A configured allowlist means the instance
already has admins, which switches the claim machinery off entirely.

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
| Mail | `mailpit` catcher | **Delete the mailpit service** and point `smtpServer` at a real relay with credentials — otherwise verification mail silently goes nowhere and nobody can finish signing up |
| Admin | first sign-in claims it | Set `VEXA_ADMIN_EMAILS` **before** exposing the instance |
| Registration | open | Decide deliberately: keep it open, restrict to invite-only, or federate and turn it off |

Keycloak runs on its **own** postgres (`keycloak-db`), deliberately not the stack's: the identity
store stays blast-radius isolated from meeting data, and enabling or removing this overlay never
migrates the `vexa` database.

### The issuer trap

`KC_HOSTNAME` pins the `iss` claim Keycloak stamps into every token. If the browser reaches Keycloak
at one address and a server-side call reaches it at another, the issuer will not match what the token
carries and tokens get rejected as invalid — with no obvious error. Keep `KEYCLOAK_PUBLIC_URL`,
`KEYCLOAK_ISSUER` and the address users actually type in agreement.
