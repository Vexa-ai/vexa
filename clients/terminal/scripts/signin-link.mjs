#!/usr/bin/env node
/** signin-link.mjs [email] — print a one-time sign-in link for this terminal, for its operator.
 *
 *  The first-run door of an instance with no mail and no OAuth (Lite): the operator, who can already
 *  read the container's log or exec into it, gets a link that signs one person in. It is the emailed
 *  door's own token (`magicTokenMint.mjs`), so the redeem route verifies it, burns it on first use and
 *  asks admin-api whether that person may sign in, exactly as for an emailed link. Nothing about it is
 *  weaker than the mail: the mail went to the person, this goes to whoever runs the box.
 *
 *  email      default self-host@vexa.ai, the person Lite provisions
 *  env        NEXTAUTH_SECRET / MAGIC_LINK_SECRET (the link key), TERMINAL_PUBLIC_URL or NEXTAUTH_URL
 *             (the origin the link opens). The link lives MAX_TTL_SECONDS (an hour), the longest any
 *             link may.
 *
 *  Prints the link on stdout and nothing else. Exits 1, saying why on stderr, when there is no
 *  usable link key, no public URL, or the emailed door is switched off (VEXA_SIGNIN_METHODS).
 */
import { MAX_TTL_SECONDS, mintToken } from "../src/app/api/auth/magicTokenMint.mjs";
import { signinMethodEnabled } from "../src/app/api/auth/oidcConfig.mjs";

export const DEFAULT_EMAIL = "self-host@vexa.ai";

/** The link, or `{ error }`.
 *  @param {string} email @param {Record<string, string | undefined>} env @param {{ now?: number }} [opts]
 *  @returns {{ url?: string, expiresAt?: number, error?: string }} */
export function signinLink(email, env, opts = {}) {
  if (!signinMethodEnabled("email", env)) {
    return { error: "the link door is off (VEXA_SIGNIN_METHODS leaves out email); sign in with a configured provider" };
  }
  const base = String(env.TERMINAL_PUBLIC_URL || env.NEXTAUTH_URL || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^/]+/.test(base)) return { error: "TERMINAL_PUBLIC_URL (or NEXTAUTH_URL) is not an http(s) origin" };
  const minted = mintToken(email, env, { ttl: MAX_TTL_SECONDS, now: opts.now, operator: true });
  if (!minted.ok) return { error: minted.error };
  return { url: `${base}/api/auth/redeem?t=${encodeURIComponent(minted.token)}`, expiresAt: minted.expiresAt };
}

const isMain = process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href;
if (isMain) {
  const email = (process.argv[2] || DEFAULT_EMAIL).trim().toLowerCase();
  const out = signinLink(email, process.env);
  if (out.error) {
    process.stderr.write(`signin-link: ${out.error}\n`);
    process.exit(1);
  }
  process.stdout.write(`${out.url}\n`);
}
