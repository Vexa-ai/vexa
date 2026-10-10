/** The terminal's signing secrets — what makes one usable, and the key the emailed link is signed with.
 *
 *  Plain JavaScript on purpose: `server.mjs` imports it to refuse to start, before Next is loaded,
 *  and the TypeScript routes import the same file, so both read one rule.
 *
 *  A secret is usable when it is set, at least 32 bytes long, and not a value that has appeared in
 *  this repository as a default or an example: anything published there is known to everybody.
 *
 *  The magic-link key is never the session secret itself. `MAGIC_LINK_SECRET` when it is configured;
 *  otherwise a key derived from `NEXTAUTH_SECRET` under a fixed label (HMAC-SHA256), so a link and a
 *  NextAuth cookie are never signed with the same key.
 */
import { createHmac } from "node:crypto";

/** The shortest secret either variable may hold, in bytes. */
export const MIN_SECRET_BYTES = 32;

/** Values that have appeared in this repository as a default or an example for NEXTAUTH_SECRET,
 *  JWT_SECRET or a sibling secret. Compared trimmed and case-folded. */
export const PUBLISHED_SECRETS = Object.freeze([
  "dev-nextauth-secret",
  "vexa-dev-nextauth-secret",
  "vexa-dev-secret",
  "vexa-dev-jwt-secret",
  "vexa-dash-nextauth-secret-dev",
  "vexa-dash-jwt-secret-dev",
  "vexa-lite-nextauth-secret",
  "vexa-lite-jwt-secret",
  "default-secret-change-me",
  "change-me",
  "change_me",
  "changeme",
  "secret",
]);

const PUBLISHED = new Set(PUBLISHED_SECRETS);

/** The label the magic-link key is derived under. Changing it invalidates every outstanding link. */
const MAGIC_LINK_LABEL = "vexa-terminal/magic-link/v1";

/** Why `value` cannot be used as the secret `name`, or null when it can.
 *  @param {unknown} value
 *  @param {string} [name]
 *  @returns {string | null} */
export function secretProblem(value, name = "NEXTAUTH_SECRET") {
  const v = typeof value === "string" ? value : "";
  if (!v.trim()) return `${name} is not set`;
  if (PUBLISHED.has(v.trim().toLowerCase())) return `${name} is a value published in the Vexa repository`;
  if (Buffer.byteLength(v, "utf8") < MIN_SECRET_BYTES) return `${name} is shorter than ${MIN_SECRET_BYTES} bytes`;
  return null;
}

/** The reason the terminal must not start with this environment, or null when it may.
 *  @param {Record<string, string | undefined>} env
 *  @returns {string | null} */
export function authSecretStartupError(env) {
  const how = "set it to a random value of at least 32 bytes, e.g. `openssl rand -hex 32`";
  const session = secretProblem(env.NEXTAUTH_SECRET, "NEXTAUTH_SECRET");
  if (session) return `${session}; ${how}`;
  if (env.MAGIC_LINK_SECRET) {
    const link = secretProblem(env.MAGIC_LINK_SECRET, "MAGIC_LINK_SECRET");
    if (link) return `${link}; ${how}, or unset it`;
    if (env.MAGIC_LINK_SECRET === env.NEXTAUTH_SECRET) return "MAGIC_LINK_SECRET must differ from NEXTAUTH_SECRET, or be unset";
  }
  return null;
}

/** The key emailed sign-in links are signed and verified with, or null when there is no usable one
 *  (the emailed door is then closed, never open).
 *  @param {Record<string, string | undefined>} env
 *  @returns {string | null} */
export function magicLinkKey(env) {
  const configured = env.MAGIC_LINK_SECRET || "";
  if (configured) {
    return secretProblem(configured, "MAGIC_LINK_SECRET") || configured === env.NEXTAUTH_SECRET ? null : configured;
  }
  const session = env.NEXTAUTH_SECRET || "";
  if (secretProblem(session)) return null;
  return createHmac("sha256", session).update(MAGIC_LINK_LABEL).digest("hex");
}
