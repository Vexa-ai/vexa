/**
 * createFakeAuthBackend — an in-memory AuthBackend (no network, no jwt, no OAuth provider).
 *
 * Holds users + minted tokens in maps, so the session core can be developed/tested without a live
 * admin API or SMTP/OAuth. find-or-create, token minting, magic-link verification, and OAuth exchange
 * are all modelled in memory:
 *   • findUserByEmail  → map lookup (null on miss — the create half of find-or-create then runs).
 *   • createUser       → mints a User with a stable id + the VexaUserData-shaped floor.
 *   • createToken      → mints a deterministic `tok-<userId>-<n>` and records it.
 *   • verifyMagicLink  → decodes a fake `magic:<email>` token (throws on a malformed/expired one).
 *   • exchangeOAuth    → decodes a fake `<provider>:<email>` code (throws on a malformed one).
 *
 * Seed with existing users (e.g. `{ users: [{ email: "anna@vexa.ai" }] }`) to exercise the
 * find-existing branch; everything else is find-or-create on the fly.
 */
import type { AuthBackend, OAuthProvider, User } from "./ports.js";

export interface FakeAuthSeed {
  /** Pre-existing users. Each is normalised to the full User floor; only `email` is required. */
  users?: Array<Partial<User> & { email: string }>;
}

/** The fake magic-link token shape: `magic:<email>`. (A real backend signs/verifies a JWT here.) */
const MAGIC_PREFIX = "magic:";
/** The fake OAuth code shape: `<provider>:<email>` (e.g. `google:anna@vexa.ai`). */

let userSeq = 0;

function makeUser(email: string, overrides?: Partial<User>): User {
  return {
    // overrides first so explicit id/email/name below always win and the floor is never undefined
    ...overrides,
    id: overrides?.id != null ? String(overrides.id) : `usr-${++userSeq}`,
    email,
    name: overrides?.name ?? email.split("@")[0],
    max_concurrent_bots: overrides?.max_concurrent_bots ?? 3,
    created_at: overrides?.created_at ?? new Date().toISOString(),
  };
}

export function createFakeAuthBackend(seed?: FakeAuthSeed): AuthBackend {
  // Keyed by lowercased email for case-insensitive find-or-create.
  const usersByEmail = new Map<string, User>();
  // Every minted token → the userId it was issued for (so a test can assert the token belongs to the user).
  const tokensByValue = new Map<string, string>();
  let tokenSeq = 0;

  for (const u of seed?.users ?? []) {
    const user = makeUser(u.email, u);
    usersByEmail.set(u.email.toLowerCase(), user);
  }

  return {
    async findUserByEmail(email: string): Promise<User | null> {
      return usersByEmail.get(email.toLowerCase()) ?? null;
    },

    async createUser(email: string): Promise<User> {
      const existing = usersByEmail.get(email.toLowerCase());
      if (existing) return existing; // idempotent — mirrors the admin API's create-after-race tolerance
      const user = makeUser(email);
      usersByEmail.set(email.toLowerCase(), user);
      return user;
    },

    async createToken(userId: string): Promise<string> {
      const token = `tok-${userId}-${++tokenSeq}`;
      tokensByValue.set(token, userId);
      return token;
    },

    async verifyMagicLink(token: string): Promise<{ email: string }> {
      if (!token.startsWith(MAGIC_PREFIX)) {
        throw new Error("fake auth: invalid magic-link token");
      }
      const email = token.slice(MAGIC_PREFIX.length);
      if (!email.includes("@")) throw new Error("fake auth: malformed magic-link token");
      return { email };
    },

    async exchangeOAuth(provider: OAuthProvider, code: string): Promise<{ email: string }> {
      const prefix = `${provider}:`;
      if (!code.startsWith(prefix)) {
        throw new Error(`fake auth: OAuth code does not match provider ${provider}`);
      }
      const email = code.slice(prefix.length);
      if (!email.includes("@")) throw new Error("fake auth: malformed OAuth code");
      return { email };
    },
  };
}
