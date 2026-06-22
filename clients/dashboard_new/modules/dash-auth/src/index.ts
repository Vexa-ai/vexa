/**
 * @vexa/dash-auth — the identity/session core behind a PORT.
 *
 * The dashboard holds ONE session object (`AuthSession`) and reads identity through it, never through
 * `fetch`/`jwt`/`next-auth`. This brick owns the SESSION/TOKEN logic — token resolution and the
 * login/logout flows — and pushes the heavy provider wiring out to a thin `AuthBackend` adapter
 * (admin API + JWT magic-link + OAuth), swappable with the in-memory `createFakeAuthBackend`.
 *
 * Token resolution (the rule every getter obeys):
 *     explicit session token  →  else selfHostToken  →  else null
 * The login* flows all share one shape: resolve an email → find-or-create the user via the backend →
 * mint a token via the backend → store it as the explicit session token. This mirrors the vendored
 * dashboard's /auth/verify + NextAuth signIn callback (find-or-create then createUserToken), with the
 * email-source step (jwt verify vs OAuth exchange) delegated to the backend port.
 */
export type {
  AuthBackend,
  AuthSession,
  CreateAuthSessionOptions,
  OAuthProvider,
  User,
} from "./ports.js";

export { createFakeAuthBackend } from "./fakes.js";
export type { FakeAuthSeed } from "./fakes.js";

import type {
  AuthBackend,
  AuthSession,
  CreateAuthSessionOptions,
  OAuthProvider,
  User,
} from "./ports.js";

/**
 * Find-or-create a user for `email`, then mint a token for them. The shared tail of every login flow
 * (magic-link + OAuth) — identical to the vendored verify/NextAuth path: findUserByEmail, create on
 * miss, then createUserToken.
 */
async function resolveUserAndToken(
  backend: AuthBackend,
  email: string,
): Promise<{ user: User; token: string }> {
  const existing = await backend.findUserByEmail(email);
  const user = existing ?? (await backend.createUser(email));
  const token = await backend.createToken(user.id);
  return { user, token };
}

/**
 * Create the dashboard's auth session.
 *
 * Returns an `AuthSession` whose token resolution is: explicit session token (set by a successful
 * login*) → else the configured `selfHostToken` → else null. `getUser()` returns the logged-in user
 * or null; there is no synthetic user for the self-host token (the token IS the identity there).
 */
export function createAuthSession(opts: CreateAuthSessionOptions): AuthSession {
  const { backend } = opts;
  const selfHostToken = opts.selfHostToken ?? null;

  // The explicit session state, set by login*/cleared by logout. Null until a user logs in.
  let sessionToken: string | null = null;
  let sessionUser: User | null = null;

  const getToken = (): string | null => sessionToken ?? selfHostToken ?? null;
  const getUser = (): User | null => sessionUser;
  const isAuthenticated = (): boolean => getToken() !== null;

  return {
    getToken,
    getUser,
    isAuthenticated,

    async loginSelfHost(): Promise<string | null> {
      // Self-host has no per-user flow: the configured token IS the session. Nothing to mint; we
      // simply surface the resolved token (selfHostToken, since no explicit login has run).
      return getToken();
    },

    async loginMagicLink(token: string): Promise<User> {
      const { email } = await backend.verifyMagicLink(token);
      const { user, token: apiToken } = await resolveUserAndToken(backend, email);
      sessionToken = apiToken;
      sessionUser = user;
      return user;
    },

    async loginOAuth(provider: OAuthProvider, code: string): Promise<User> {
      const { email } = await backend.exchangeOAuth(provider, code);
      const { user, token: apiToken } = await resolveUserAndToken(backend, email);
      sessionToken = apiToken;
      sessionUser = user;
      return user;
    },

    async logout(): Promise<void> {
      sessionToken = null;
      sessionUser = null;
    },
  };
}
