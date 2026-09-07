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
 *      payload = base64url(JSON {e: <email>, x: <expiry, epoch seconds>, j: <jti>})
 *      sig     = base64url(HMAC-SHA256(NEXTAUTH_SECRET, payload))
 *  Signatures are compared with timingSafeEqual. With no NEXTAUTH_SECRET nothing can be minted
 *  OR verified (fail closed): an unconfigured deploy has no magic-link door at all, rather than
 *  an unsigned one that anybody could forge.
 *
 *  TTL: 15 minutes by default (MAGIC_LINK_TTL_SECONDS overrides).
 *
 *  SINGLE USE: a redeemed `jti` is remembered until the token would have expired anyway, so a
 *  link works exactly once. See `consumeJti` for the multi-replica caveat.
 */
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

/** Default lifetime of an emailed link — long enough to walk to a phone, short enough that a
 *  forwarded/leaked mail stops being a credential quickly. */
export const DEFAULT_TTL_SECONDS = 15 * 60;

export function ttlSeconds(): number {
  const raw = parseInt(process.env.MAGIC_LINK_TTL_SECONDS || "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TTL_SECONDS;
}

/** The signing key. Read at call time (not as a module constant) so tests and the server observe
 *  the live env. Empty/absent → the door is closed, not open. */
function secret(): string | null {
  const s = process.env.NEXTAUTH_SECRET || "";
  return s.trim() ? s : null;
}

function b64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(s: string): Buffer {
  return Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
}

function signPayload(payload: string, key: string): string {
  return b64url(createHmac("sha256", key).update(payload).digest());
}

export type MintResult =
  | { ok: true; token: string; jti: string; expiresAt: number }
  | { ok: false; error: string };

/** Sign a link for `email`. `now`/`ttl` are injectable so expiry is testable without sleeping. */
export function mintMagicToken(email: string, opts: { ttl?: number; now?: number } = {}): MintResult {
  const key = secret();
  if (!key) return { ok: false, error: "NEXTAUTH_SECRET is not configured — magic links are disabled" };
  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  const expiresAt = nowSec + (opts.ttl ?? ttlSeconds());
  const jti = randomUUID();
  const payload = b64url(Buffer.from(JSON.stringify({ e: email, x: expiresAt, j: jti }), "utf8"));
  return { ok: true, token: `${payload}.${signPayload(payload, key)}`, jti, expiresAt };
}

export type VerifyFailure = "unconfigured" | "malformed" | "bad-signature" | "expired" | "used";
export type VerifyResult =
  | { ok: true; email: string; jti: string; expiresAt: number }
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

  let claims: { e?: unknown; x?: unknown; j?: unknown };
  try {
    claims = JSON.parse(unb64url(payload).toString("utf8"));
  } catch {
    return { ok: false, reason: "malformed" };
  }
  const email = typeof claims.e === "string" ? claims.e : "";
  const jti = typeof claims.j === "string" ? claims.j : "";
  const expiresAt = typeof claims.x === "number" ? claims.x : NaN;
  if (!email || !jti || !Number.isFinite(expiresAt)) return { ok: false, reason: "malformed" };

  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  if (nowSec >= expiresAt) return { ok: false, reason: "expired" };

  return { ok: true, email, jti, expiresAt };
}

// ── single-use ledger ────────────────────────────────────────────────────────────────────────
/** Redeemed jti → the epoch-second after which it can be forgotten (the token's own expiry; past
 *  that the signature check refuses it anyway, so the ledger never needs to grow past one TTL).
 *
 *  ⚠ PROCESS-LOCAL. This is an in-memory Map, so single-use holds only within one replica:
 *  with N terminal replicas behind a load balancer a link could be redeemed up to N times, and a
 *  container restart forgets the ledger entirely (bounded by the 15-minute TTL either way). The
 *  minutes terminal runs as a SINGLE container today, which is why this is acceptable. Scaling
 *  out means moving the ledger to shared state — Redis, or a `used_jti` row in admin-api — and
 *  the swap is confined to `consumeJti` below. */
const redeemed = new Map<string, number>();

function sweep(nowSec: number): void {
  for (const [jti, forgetAfter] of redeemed) if (forgetAfter <= nowSec) redeemed.delete(jti);
}

/** Burn a jti. Returns false if it was already burned (i.e. the link is being replayed). */
export function consumeJti(jti: string, expiresAt: number, now = Date.now()): boolean {
  const nowSec = Math.floor(now / 1000);
  sweep(nowSec);
  if (redeemed.has(jti)) return false;
  redeemed.set(jti, expiresAt);
  return true;
}

/** Test seam — empties the ledger. Never called by the routes. */
export function _resetJtiLedger(): void {
  redeemed.clear();
}

/** Verify AND burn, atomically from the caller's point of view: the ONLY entry point that may
 *  authorise a sign-in. */
export function redeemMagicToken(token: string, opts: { now?: number } = {}): VerifyResult {
  const v = verifyMagicToken(token, opts);
  if (!v.ok) return v;
  if (!consumeJti(v.jti, v.expiresAt, opts.now ?? Date.now())) return { ok: false, reason: "used" };
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
