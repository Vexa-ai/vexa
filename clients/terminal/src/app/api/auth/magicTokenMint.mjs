/** Minting a sign-in link token — the one rule, shared by the emailed door (`magicToken.ts`) and the
 *  operator's printed first-run link (`scripts/signin-link.mjs`, Lite's `vexa-signin-link`).
 *
 *  Plain JavaScript on purpose, like `authSecret.mjs`: a node script outside Next reads the same file
 *  the routes do, so a printed link and an emailed one are the same token, verified and burned by the
 *  same redeem route. The wire format and the lifetime rules are documented in `magicToken.ts`.
 */
import { createHmac, randomUUID } from "node:crypto";
import { magicLinkKey } from "./authSecret.mjs";

/** Default lifetime of a link. */
export const DEFAULT_TTL_SECONDS = 15 * 60;

/** The longest a link may live, whatever MAGIC_LINK_TTL_SECONDS or the caller says. */
export const MAX_TTL_SECONDS = 60 * 60;

/** @param {Buffer} buf */
export function b64url(buf) {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** @param {string} payload @param {string} key */
export function signPayload(payload, key) {
  return b64url(createHmac("sha256", key).update(payload).digest());
}

/** MAGIC_LINK_TTL_SECONDS, capped at MAX_TTL_SECONDS; the default when unset or not a number.
 *  @param {Record<string, string | undefined>} env */
export function ttlSeconds(env) {
  const raw = parseInt(env?.MAGIC_LINK_TTL_SECONDS || "", 10);
  return Number.isFinite(raw) && raw > 0 ? Math.min(raw, MAX_TTL_SECONDS) : DEFAULT_TTL_SECONDS;
}

/** Sign a link for `email` with the key `env` configures.
 *
 *  `binding` is the claim that ties the link to the browser that asked (`magicToken.ts`,
 *  `bindingClaim`); `""` leaves it unbound, which verify admits only for a test user. `operator`
 *  marks the link the operator prints from the instance's own shell: unbound, and admitted for any
 *  address, because no browser asked for it and whoever reads it already runs the box.
 *  @param {string} email
 *  @param {Record<string, string | undefined>} env
 *  @param {{ ttl?: number, now?: number, binding?: string, operator?: boolean }} [opts]
 *  @returns {{ ok: true, token: string, jti: string, expiresAt: number } | { ok: false, error: string }} */
export function mintToken(email, env, opts = {}) {
  const key = magicLinkKey(env);
  if (!key) return { ok: false, error: "no usable NEXTAUTH_SECRET (or MAGIC_LINK_SECRET) — magic links are disabled" };
  const operator = opts.operator === true;
  if (operator ? opts.binding !== undefined : typeof opts.binding !== "string") {
    return { ok: false, error: "a sign-in link is bound to the browser that asked for it, or printed for the operator" };
  }
  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  const expiresAt = nowSec + Math.min(opts.ttl ?? ttlSeconds(env), MAX_TTL_SECONDS);
  const jti = randomUUID();
  const claims = operator ? { e: email, x: expiresAt, j: jti, b: "", o: 1 } : { e: email, x: expiresAt, j: jti, b: opts.binding };
  const payload = b64url(Buffer.from(JSON.stringify(claims), "utf8"));
  return { ok: true, token: `${payload}.${signPayload(payload, key)}`, jti, expiresAt };
}
