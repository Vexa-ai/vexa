/**
 * The identity/session PORT — the seam @vexa/dash-auth owns.
 *
 * The brick owns the SESSION/TOKEN logic (token resolution, find-or-create, login/logout flows). The
 * heavy provider wiring (admin-API HTTP, JWT magic-link signing/verification, NextAuth/OAuth provider
 * round-trips) is a THIN adapter behind this `AuthBackend` interface. The session core talks only to
 * this port, never to `fetch`/`jwt`/`next-auth` directly — so a real backend and the in-memory fake
 * (`createFakeAuthBackend`) are swappable without touching the session logic.
 *
 * Behaviour grounded on the vendored dashboard auth surface
 * (`clients/dashboard/src/lib/vexa-admin-api.ts` + `app/api/auth/{verify,send-magic-link,oauth-callback}`):
 *   findUserByEmail / createUser / createUserToken → the admin-API user+token primitives.
 *   verifyMagicLink → the `jwt.verify(token)` step of /auth/verify (returns the embedded email).
 *   exchangeOAuth  → the provider code→email step of the NextAuth signIn callback / oauth-callback.
 */

/**
 * The authenticated user the session exposes. Mirrors the admin-API `VexaUserData` floor the vendored
 * dashboard carries through every auth flow (`id`, `email`, `name`, `max_concurrent_bots`,
 * `created_at`); extra keys are allowed — the shape is additive.
 */
export interface User {
  id: string;
  email: string;
  name: string;
  max_concurrent_bots?: number;
  created_at?: string;
  [k: string]: unknown;
}

/** A supported OAuth/SSO provider id (open string — the adapter maps it to a concrete provider). */
export type OAuthProvider = "google" | "azure-ad" | string;

/**
 * The identity backend the session core depends on. Every method is one provider primitive; the
 * session core composes them into the login/logout/token-resolution flows. A real implementation is a
 * thin adapter over the admin API + JWT + OAuth provider; `createFakeAuthBackend` is the in-memory
 * test/dev double.
 */
export interface AuthBackend {
  /** Look up an existing user by email. Resolves to `null` when no such user exists (not an error). */
  findUserByEmail(email: string): Promise<User | null>;
  /** Provision a new user for `email` (find-or-create's create half). */
  createUser(email: string): Promise<User>;
  /** Mint a session/API token for a user id. */
  createToken(userId: string): Promise<string>;
  /** Verify a magic-link token and return the email it was issued for. Throws on an invalid/expired token. */
  verifyMagicLink(token: string): Promise<{ email: string }>;
  /** Exchange an OAuth provider's auth code for the verified account email. Throws on a failed exchange. */
  exchangeOAuth(provider: OAuthProvider, code: string): Promise<{ email: string }>;
}

/** What `createAuthSession` returns — the SESSION the dashboard holds. */
export interface AuthSession {
  /** The current token: explicit session token → else selfHostToken → else null. */
  getToken(): string | null;
  /** The current logged-in user, or null when there is no session user. */
  getUser(): User | null;
  /** True when a token resolves (session token or selfHostToken). */
  isAuthenticated(): boolean;
  /** Self-host login: there is no per-user flow — the configured selfHostToken IS the session. */
  loginSelfHost(): Promise<string | null>;
  /** Magic-link login: verify token → email → find-or-create user → mint session token. */
  loginMagicLink(token: string): Promise<User>;
  /** OAuth login: exchange code → email → find-or-create user → mint session token. */
  loginOAuth(provider: OAuthProvider, code: string): Promise<User>;
  /** Clear the explicit session token + user. (selfHostToken, if any, still resolves afterwards.) */
  logout(): Promise<void>;
}

/** Options for `createAuthSession`. */
export interface CreateAuthSessionOptions {
  /** The identity backend (real adapter or fake). */
  backend: AuthBackend;
  /**
   * Self-host fallback token. When set and no explicit session token exists, this resolves as the
   * token (`isAuthenticated()` is true). Mirrors the self-hosted single-key deploy where the API key
   * IS the identity. Optional — the hosted dashboard leaves it unset and relies on login flows.
   */
  selfHostToken?: string | null;
}
