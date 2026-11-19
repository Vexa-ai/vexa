/** Magic-link tokens — the emailed door.
 *
 *  One link is BOTH the door and the destination:
 *      /api/auth/redeem?t=<token>&next=<relative-path>
 *  The token is a signed statement "this address asked for a link at time T". Control of the
 *  MAILBOX is the proof of identity — exactly what the login route's own comment always described
 *  ("the recipient's own address IS the identity (prod = a signed token)"), now actually signed.
 *
 *  Wire format — two base64url parts, dot-separated:
 *      <payload>.<sig>
 *      payload = base64url(JSON {e: <email>, x: <expiry, epoch seconds>, j: <jti>, b: <binding>[, o: 1]})
 *      sig     = base64url(HMAC-SHA256(<link key>, payload))
 *  The link key is never the session secret: `MAGIC_LINK_SECRET` when configured, else a key
 *  derived from `NEXTAUTH_SECRET` (`./authSecret.mjs`). Signatures are compared with
 *  timingSafeEqual. With no usable secret — unset, shorter than 32 bytes, or a value published in
 *  the repository — nothing can be minted OR verified (fail closed): such a deploy has no
 *  magic-link door at all, rather than one anybody could sign for.
 *
 *  TTL: 15 minutes by default (MAGIC_LINK_TTL_SECONDS overrides, up to MAX_TTL_SECONDS). Verify
 *  also refuses an expiry further out than MAX_TTL_SECONDS, whatever the token says.
 *
 *  SINGLE USE: admin-api remembers a redeemed `jti` until the token would have expired anyway, for
 *  every terminal replica at once, so a link works exactly once (`redeemMagicToken`).
 *
 *  BOUND TO THE BROWSER THAT ASKED (S03T-3). Asking for a link sets `vexa-signin-bind`, an httpOnly
 *  cookie holding a random value, on the browser that asked; the link carries the SHA-256 of that
 *  value (claim `b`), signed with the rest. The link signs in only the browser that holds the cookie
 *  (`browserHoldsLink`). Without this, anybody could ask for a link to their OWN address, forward it,
 *  and have whoever pressed Sign in working in the sender's account (login CSRF). A link opened in
 *  another browser shows why it cannot be used there and spends nothing. The one unbound link is a
 *  reserved-domain test user's, which an instance administrator mints by hand
 *  (`../test-users/link`, `@example.test` only).
 */
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { redeemSigninLink } from "./adminApi";
import { magicLinkKey } from "./authSecret.mjs";
import {
  b64url,
  DEFAULT_TTL_SECONDS,
  MAX_TTL_SECONDS,
  mintToken,
  signPayload,
  ttlSeconds as ttlFromEnv,
} from "./magicTokenMint.mjs";

/** Default lifetime of an emailed link — long enough to walk to a phone, short enough that a
 *  forwarded/leaked mail stops being a credential quickly. The longest one may live is
 *  MAX_TTL_SECONDS, whatever MAGIC_LINK_TTL_SECONDS says. Minting is `magicTokenMint.mjs`, shared
 *  with the operator's printed first-run link. */
export { DEFAULT_TTL_SECONDS, MAX_TTL_SECONDS };

export function ttlSeconds(): number {
  return ttlFromEnv(process.env);
}

/** The signing key. Read at call time (not as a module constant) so tests and the server observe
 *  the live env. No usable secret → the door is closed, not open. */
function secret(): string | null {
  return magicLinkKey(process.env);
}

function unb64url(s: string): Buffer {
  return Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
}

// ── the browser that asked ────────────────────────────────────────────────────────────────
/** The httpOnly cookie that ties a link to the browser that asked for it. */
export const BIND_COOKIE = "vexa-signin-bind";
const BIND_VALUE = /^[A-Za-z0-9_-]{43}$/;
/** The only address a link may be minted for without a browser: the reserved test domain. */
const UNBOUND_ADDRESS = /^[a-z0-9._+-]+@example\.test$/;

/** This browser's binding value: the one its cookie already holds, so every link it asked for
 *  stays good, or a fresh 256-bit one. */
export function browserBinding(existing: string | null | undefined): string {
  return typeof existing === "string" && BIND_VALUE.test(existing) ? existing : b64url(randomBytes(32));
}

/** What a link carries for a binding value: its SHA-256, so the mail never holds the cookie. */
export function bindingClaim(value: string): string {
  return b64url(createHash("sha256").update(value, "utf8").digest());
}

/** Does the browser whose cookie holds `cookie` hold this verified link? An unbound link (a test
 *  user's) is held by any browser. */
export function browserHoldsLink(v: { binding: string }, cookie: string | null | undefined): boolean {
  if (!v.binding) return true;
  if (typeof cookie !== "string" || !BIND_VALUE.test(cookie)) return false;
  const want = Buffer.from(v.binding, "utf8");
  const have = Buffer.from(bindingClaim(cookie), "utf8");
  return want.length === have.length && timingSafeEqual(want, have);
}

export type MintResult =
  | { ok: true; token: string; jti: string; expiresAt: number }
  | { ok: false; error: string };

/** Sign a link for `email`, bound to the browser whose binding value is `browser`. `null` mints an
 *  unbound link, and only for a reserved-domain test address. `now`/`ttl` are injectable so expiry
 *  is testable without sleeping. */
export function mintMagicToken(email: string, browser: string | null, opts: { ttl?: number; now?: number } = {}): MintResult {
  if (!secret()) return { ok: false, error: "no usable NEXTAUTH_SECRET (or MAGIC_LINK_SECRET) — magic links are disabled" };
  if (browser === null ? !UNBOUND_ADDRESS.test(email) : !BIND_VALUE.test(browser)) {
    return { ok: false, error: "a sign-in link is bound to the browser that asked for it" };
  }
  return mintToken(email, process.env, { ...opts, binding: browser === null ? "" : bindingClaim(browser) }) as MintResult;
}

/** `unavailable`: the link verified, but whether it was already used could not be learned (admin-api
 *  down or unconfigured). It refuses, and the link is not spent. `other-browser`: the link is good,
 *  but it was asked for in another browser (the route decides this, `browserHoldsLink`). */
export type VerifyFailure = "unconfigured" | "malformed" | "bad-signature" | "expired" | "used" | "unavailable" | "other-browser";
export type VerifyResult =
  | { ok: true; email: string; jti: string; expiresAt: number; binding: string }
  | { ok: false; reason: VerifyFailure };

/** Signature + expiry only — PURE, and it does NOT consume the jti. Callers that actually let
 *  somebody in must use `redeemMagicToken`, which additionally burns the jti. */
export function verifyMagicToken(token: string, opts: { now?: number } = {}): VerifyResult {
  const key = secret();
  if (!key) return { ok: false, reason: "unconfigured" };
  if (typeof token !== "string") return { ok: false, reason: "malformed" };

  const parts = token.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return { ok: false, reason: "malformed" };
  const [payload, sig] = parts;

  const expected = Buffer.from(signPayload(payload, key), "utf8");
  const given = Buffer.from(sig, "utf8");
  // timingSafeEqual throws on length mismatch — a length difference is already a mismatch.
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
    return { ok: false, reason: "bad-signature" };
  }

  let claims: { e?: unknown; x?: unknown; j?: unknown; b?: unknown; o?: unknown };
  try {
    claims = JSON.parse(unb64url(payload).toString("utf8"));
  } catch {
    return { ok: false, reason: "malformed" };
  }
  const email = typeof claims.e === "string" ? claims.e : "";
  const jti = typeof claims.j === "string" ? claims.j : "";
  const expiresAt = typeof claims.x === "number" ? claims.x : NaN;
  if (!email || !jti || !Number.isFinite(expiresAt)) return { ok: false, reason: "malformed" };
  // Every link names the browser it belongs to. Two may name none: a test user's, and the one the
  // operator prints from the box's own shell (`o: 1`, `scripts/signin-link.mjs`), where no browser
  // has asked and whoever reads it already runs the instance. No route mints either kind for a
  // request.
  const binding = typeof claims.b === "string" ? claims.b : null;
  const operator = claims.o === 1;
  if (binding === null || (!binding && !operator && !UNBOUND_ADDRESS.test(email))) return { ok: false, reason: "malformed" };
  if (binding && operator) return { ok: false, reason: "malformed" };

  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  if (nowSec >= expiresAt) return { ok: false, reason: "expired" };
  // No link this server mints lives longer than MAX_TTL_SECONDS, so an expiry further out is not
  // one of ours, whatever signed it.
  if (expiresAt - nowSec > MAX_TTL_SECONDS) return { ok: false, reason: "malformed" };

  return { ok: true, email, jti, expiresAt, binding };
}

// ── single use ─────────────────────────────────────────────────────────────────────────────
/** Verify AND burn: the ONLY entry point that may authorise a sign-in.
 *
 *  The record of which links were used is admin-api's (`POST /internal/signin-links/redeem`,
 *  signin.v1), kept in the service Redis until the link would have expired anyway. It used to be a
 *  Map in this process, so with N replicas a link could be redeemed N times and a restart forgot it.
 *  Only admin-api's "first" admits; "used" is a replay; anything else — admin-api unreachable or
 *  unconfigured, its store down — is `unavailable` and refuses, with the link left unspent. */
export async function redeemMagicToken(token: string, opts: { now?: number } = {}): Promise<VerifyResult> {
  const v = verifyMagicToken(token, opts);
  if (!v.ok) return v;
  const recorded = await redeemSigninLink(v.jti, v.expiresAt);
  if (recorded === "used") return { ok: false, reason: "used" };
  if (recorded !== "first") return { ok: false, reason: "unavailable" };
  return v;
}

// ── open-redirect guard ──────────────────────────────────────────────────────────────────────
/** Reduce an untrusted `next=` to a SITE-RELATIVE path, or fall back to "/".
 *
 *  The link is emailed, so `next` is attacker-reachable: without this an emailed Vexa link could
 *  bounce the recipient to any host on the internet, wearing our domain in the mail. Accepted:
 *  a single leading "/" followed by a path/query/fragment. Refused: absolute URLs, scheme-bearing
 *  values, protocol-relative "//host", backslashes (some browsers normalise "\" to "/"), control
 *  characters (header splitting), and anything whose percent-decoded form breaks those rules. */
export function safeNext(raw: string | null | undefined, fallback = "/"): string {
  if (typeof raw !== "string") return fallback;
  const v = raw.trim();
  const hasControl = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      if (c < 0x20 || c === 0x7f) return true;
    }
    return false;
  };
  const bad = (s: string) => !s.startsWith("/") || s.startsWith("//") || s.includes("\\") || hasControl(s);
  if (!v || bad(v)) return fallback;
  let decoded: string;
  try {
    decoded = decodeURIComponent(v);
  } catch {
    return fallback; // malformed percent-encoding
  }
  if (bad(decoded)) return fallback;
  return v;
}
