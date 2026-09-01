/** Server-only admin-api client for the terminal's own auth.
 *
 *  Mirrors the dashboard's pattern (clients/dashboard/src/lib/vexa-admin-api.ts) WITHOUT importing it
 *  — the dashboard is being retired. The terminal owns a tiny slice: find-or-create a user by email and
 *  mint an APIToken (scopes bot,tx,browser). All calls carry X-Admin-API-Key and are never cached
 *  (a cached 404 would make find-or-create fabricate duplicate users).
 */

export const AUTH_COOKIE = process.env.VEXA_AUTH_COOKIE_NAME || "vexa-token";
export const USER_INFO_COOKIE = process.env.VEXA_USER_INFO_COOKIE_NAME || "vexa-user-info";

export interface AdminUser {
  id: string | number;
  email: string;
  name?: string | null;
  max_concurrent_bots?: number;
  created_at?: string;
}

export interface AdminResult<T> {
  ok: boolean;
  status: number;
  data?: T;
  notFound?: boolean;
  error?: string;
}

function adminConfig(): { url: string; key: string } | null {
  const url = (process.env.VEXA_ADMIN_API_URL || "").replace(/\/$/, "");
  const key = process.env.VEXA_ADMIN_API_KEY || "";
  if (!url || !key || key === "your_admin_api_key_here") return null;
  return { url, key };
}

async function adminRequest<T>(path: string, init: RequestInit = {}, timeout = 15000): Promise<AdminResult<T>> {
  const cfg = adminConfig();
  if (!cfg) return { ok: false, status: 503, error: "Admin API is not configured (VEXA_ADMIN_API_URL / VEXA_ADMIN_API_KEY)" };

  try {
    const res = await fetch(`${cfg.url}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Admin-API-Key": cfg.key, ...init.headers },
      cache: "no-store",
      signal: AbortSignal.timeout(timeout),
    });

    if (res.status === 404) return { ok: false, status: 404, notFound: true };
    if (!res.ok) {
      const detail = (await res.text().catch(() => "")).slice(0, 500);
      return { ok: false, status: res.status, error: detail || `admin-api returned ${res.status}` };
    }
    if (res.status === 204) return { ok: true, status: 204 };
    return { ok: true, status: res.status, data: (await res.json()) as T };
  } catch (err) {
    const e = err as Error;
    return { ok: false, status: 0, error: e.name === "TimeoutError" ? "admin-api request timed out" : e.message };
  }
}

export function findUserByEmail(email: string): Promise<AdminResult<AdminUser>> {
  return adminRequest<AdminUser>(`/admin/users/email/${encodeURIComponent(email)}`, { method: "GET" });
}

export function createUser(email: string): Promise<AdminResult<AdminUser>> {
  return adminRequest<AdminUser>(`/admin/users`, { method: "POST", body: JSON.stringify({ email }) });
}

export function createUserToken(userId: string | number): Promise<AdminResult<{ token: string }>> {
  return adminRequest<{ token: string }>(
    `/admin/users/${encodeURIComponent(String(userId))}/tokens?scopes=bot,tx,browser`,
    { method: "POST" },
  );
}

// ── verified identity — admin-api's internal oracle (`POST /internal/validate`, the same
//    X-Internal-Secret edge the gateway uses). The `vexa-token` auth cookie is the ONLY input; the
//    returned {user_id, email} is the ONLY identity this server trusts. The `vexa-user-info` cookie
//    is display-only: httpOnly stops JS reads, not a hand-crafted Cookie header, so nothing
//    security-relevant may ever be derived from it.

export type ValidatedUser =
  | { ok: true; userId: string | number; email: string; isAdmin: boolean }
  | { ok: false; status: number; error: string };

export async function validateAuthToken(token: string): Promise<ValidatedUser> {
  const url = (process.env.VEXA_ADMIN_API_URL || "").replace(/\/$/, "");
  const secret = process.env.VEXA_INTERNAL_API_SECRET || "";
  if (!url || !secret) {
    // Fail closed — an unconfigured oracle must never fall back to trusting client-sendable data.
    return { ok: false, status: 503, error: "Auth validation is not configured (VEXA_ADMIN_API_URL / VEXA_INTERNAL_API_SECRET)" };
  }

  try {
    const res = await fetch(`${url}/internal/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Internal-Secret": secret },
      body: JSON.stringify({ token }),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (res.status === 401) return { ok: false, status: 401, error: "Not authenticated" };
    if (!res.ok) return { ok: false, status: 503, error: `Token validation failed (admin-api returned ${res.status})` };
    const data = (await res.json()) as { user_id?: string | number; email?: string; is_admin?: boolean };
    if (data.user_id === undefined || data.user_id === null || !data.email) {
      return { ok: false, status: 502, error: "Token validation returned no identity" };
    }
    return { ok: true, userId: data.user_id, email: data.email, isAdmin: data.is_admin === true };
  } catch (err) {
    const e = err as Error;
    return { ok: false, status: 503, error: e.name === "TimeoutError" ? "Token validation timed out" : "Token validation unavailable" };
  }
}

// ── first-run bootstrap admin — a fresh instance has NO admin; the first successful sign-in
//    claims the role (admin-api serializes concurrent claims). A configured VEXA_ADMIN_EMAILS
//    allowlist means the instance ALREADY has admins → the claim machinery stays off entirely,
//    which also keeps existing deployments (allowlist-run) from handing admin to the next login.

function allowlistConfigured(): boolean {
  return (process.env.VEXA_ADMIN_EMAILS || "").split(",").some((e) => e.trim());
}

async function internalRequest<T>(path: string, init: RequestInit = {}): Promise<AdminResult<T>> {
  const url = (process.env.VEXA_ADMIN_API_URL || "").replace(/\/$/, "");
  const secret = process.env.VEXA_INTERNAL_API_SECRET || "";
  if (!url || !secret) {
    return { ok: false, status: 503, error: "Admin API internal edge is not configured (VEXA_ADMIN_API_URL / VEXA_INTERNAL_API_SECRET)" };
  }
  try {
    const res = await fetch(`${url}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", "X-Internal-Secret": secret, ...init.headers },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) {
      const detail = (await res.text().catch(() => "")).slice(0, 500);
      return { ok: false, status: res.status, error: detail || `admin-api returned ${res.status}` };
    }
    return { ok: true, status: res.status, data: (await res.json()) as T };
  } catch (err) {
    const e = err as Error;
    return { ok: false, status: 0, error: e.name === "TimeoutError" ? "admin-api request timed out" : e.message };
  }
}

/** Does this instance have an admin yet? An allowlist counts as "yes" (those emails ARE admins).
 *  FAIL-SAFE towards true: if the probe can't answer, the login surface shows plain sign-in
 *  rather than dangling a claim screen that can't succeed. */
export async function instanceHasAdmin(): Promise<boolean> {
  if (allowlistConfigured()) return true;
  const res = await internalRequest<{ admin_exists?: boolean }>("/internal/instance", { method: "GET" });
  if (!res.ok || !res.data) return true;
  return res.data.admin_exists === true;
}

/** Claim the admin role for this user IF the instance has none — the "first sign-in = admin"
 *  step, called on every successful login (admin-api makes it a no-op once an admin exists).
 *  BEST-EFFORT: a failure must never block sign-in; the claim screen simply reappears. */
async function bootstrapAdminClaim(userId: string | number): Promise<void> {
  if (allowlistConfigured()) return; // allowlist-run instance → role claims stay off
  const res = await internalRequest<{ claimed?: boolean }>("/internal/bootstrap-admin", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
  if (res.ok && res.data?.claimed) {
    console.info(`[terminal-auth] bootstrap: user ${userId} claimed the admin role (first sign-in)`);
  } else if (!res.ok) {
    console.warn(`[terminal-auth] bootstrap-admin claim failed (sign-in continues): ${res.error}`);
  }
}

// ── token self-serve (the /api/tokens routes) — admin-tier calls, ALWAYS scoped to the logged-in
//    user's own user_id (resolved server-side from the auth cookies; never taken from the client).

/** A token as admin-api lists it — metadata only, never the secret value. */
export interface AdminTokenInfo {
  id: number;
  user_id: number;
  scopes: string[];
  name?: string | null;
  created_at?: string | null;
  last_used_at?: string | null;
  expires_at?: string | null;
}

/** The mint response — the ONLY place the token value ever crosses. */
export interface AdminMintedToken extends AdminTokenInfo {
  token: string;
}

export function listUserTokens(userId: string | number): Promise<AdminResult<AdminTokenInfo[]>> {
  return adminRequest<AdminTokenInfo[]>(
    `/admin/users/${encodeURIComponent(String(userId))}/tokens`,
    { method: "GET" },
  );
}

export function mintUserToken(
  userId: string | number,
  opts: { scopes: string[]; name?: string; expiresIn?: number },
): Promise<AdminResult<AdminMintedToken>> {
  const q = new URLSearchParams({ scopes: opts.scopes.join(",") });
  if (opts.name) q.set("name", opts.name);
  if (opts.expiresIn && opts.expiresIn > 0) q.set("expires_in", String(opts.expiresIn));
  return adminRequest<AdminMintedToken>(
    `/admin/users/${encodeURIComponent(String(userId))}/tokens?${q.toString()}`,
    { method: "POST" },
  );
}

export function revokeToken(tokenId: string | number): Promise<AdminResult<void>> {
  return adminRequest<void>(`/admin/tokens/${encodeURIComponent(String(tokenId))}`, { method: "DELETE" });
}

// One authenticated edge: provisioning goes through the gateway (which resolves the api-key → user_id and
// injects X-User-Id), never agent-api directly. Mirrors the workspace proxy route's GATEWAY_URL default.
const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

/** EAGERLY provision the user's agent workspace tiers (Personal baseline + private `_system`) so they
 *  exist from account creation instead of being lazily seeded on the first chat. BEST-EFFORT: the call is
 *  idempotent server-side and the lazy first-dispatch path is a full fallback, so any failure here (agent
 *  down, slow, misconfig) is logged and swallowed — it must NEVER block sign-in. Authenticates with the
 *  freshly minted api-key over the gateway's `/agent/workspace/*` edge. */
async function provisionUserWorkspace(token: string): Promise<void> {
  try {
    const res = await fetch(`${GATEWAY_URL}/agent/workspace/init`, {
      method: "POST",
      headers: { "X-API-Key": token, "Content-Type": "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(12000),
    });
    if (!res.ok) {
      console.warn(`[terminal-auth] eager workspace provisioning returned ${res.status} (lazy seeding will cover it)`);
    }
  } catch (err) {
    console.warn("[terminal-auth] eager workspace provisioning failed (lazy seeding will cover it):", (err as Error).message);
  }
}

/** The stable marker on login-minted tokens — this is the ONLY set the login cap prunes.
 *  Self-serve tokens (minted via /api/tokens with a user-chosen name) carry a different name
 *  and are NEVER touched by the prune below. */
export const TERMINAL_LOGIN_TOKEN_NAME = "terminal-login";

/** How many `terminal-login` tokens a single user may keep. A cap, not a purge: a user's few
 *  genuine devices survive while a sign-in loop cannot exceed N. Configurable, default 3. */
export function terminalLoginTokenCap(): number {
  const raw = parseInt(process.env.VEXA_TERMINAL_LOGIN_TOKEN_CAP || "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 3;
}

/** A token last active inside this window is treated as a LIVE SESSION and is spared even when it
 *  sits past the cap. Configurable, default 48 hours. */
export function terminalLoginRecentUseWindowMs(): number {
  const raw = parseFloat(process.env.VEXA_TERMINAL_LOGIN_RECENT_USE_HOURS || "");
  const hours = Number.isFinite(raw) && raw > 0 ? raw : 48;
  return hours * 3600_000;
}

/** The CEILING: how far live-session protection may push the count above the cap before the prune
 *  stops honouring it. Without this the recent-use exemption is unbounded — every token in a
 *  same-day sign-in burst is by definition recent, so nothing would ever be revoked and a looping
 *  harness could mint indefinitely (which is the accumulation #638 existed to stop). Past the
 *  ceiling the least-recently-used tokens go regardless; an actively-used session is the LAST
 *  candidate in that ordering, so it survives unless there are `max` sessions more active than it.
 *  Default cap × 5. */
export function terminalLoginTokenMax(): number {
  const raw = parseInt(process.env.VEXA_TERMINAL_LOGIN_TOKEN_MAX || "", 10);
  const cap = terminalLoginTokenCap();
  if (Number.isFinite(raw) && raw > 0) return Math.max(raw, cap);
  return cap * 5;
}

/** admin-api serialises datetimes NAIVE-UTC — `2026-09-01T16:33:46.315228`, no zone designator
 *  (`datetime.utcnow()` straight through Pydantic). `Date.parse` reads a zone-less DATE-TIME form
 *  as LOCAL time, so on any host whose TZ is not UTC the value lands hours off. That skew cancels
 *  in a relative sort (every value is shifted the same way) but NOT in the absolute
 *  recent-use window below, which compares against a real `Date.now()`. Stamp the zone when the
 *  string doesn't carry one. Returns NaN for absent/unparseable input. */
export function parseAdminTimestamp(raw: string | null | undefined): number {
  if (typeof raw !== "string" || !raw.trim()) return NaN;
  const s = raw.trim();
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s);
  return Date.parse(zoned ? s : `${s}Z`);
}

/** When a token was last ACTIVE: its last authenticated request, or — if it has never been used —
 *  the moment it was issued. A freshly minted token is therefore active by construction, which is
 *  what keeps the sign-in that just happened from pruning itself. */
function lastActiveAt(t: AdminTokenInfo): number {
  const used = parseAdminTimestamp(t.last_used_at);
  if (Number.isFinite(used)) return used;
  const made = parseAdminTimestamp(t.created_at);
  return Number.isFinite(made) ? made : 0;
}

/** BEST-EFFORT: after a login mint, bound the user's `terminal-login` tokens — by LAST USE, never
 *  by issue age.
 *
 *  ⚠ THE BUG THIS REPLACES (2026-09-01). The previous policy sorted by `created_at` and revoked the
 *  OLDEST-ISSUED tokens over the cap. That makes the longest-lived session the first casualty of
 *  everybody else's sign-ins: while agents redeemed magic links against this deploy all day, each
 *  redeem minted a token, and the founder's months-old browser session was every single time the
 *  oldest one. His tab was revoked repeatedly — every API call 401ing under a UI that still
 *  rendered. Issue age is exactly backwards as a proxy for "who needs this least".
 *
 *  THE POLICY NOW, in order:
 *   1. Rank the login tokens by LAST ACTIVITY (`last_used_at`, falling back to `created_at` for a
 *      token that has never authenticated anything) — least-recently-used first. This alone fixes
 *      the founder: a tab making requests is always at the newest end of that order, so it never
 *      lands in the eviction slice at all.
 *   2. Everything past the cap, taken from the least-recently-used end, is a candidate.
 *   3. A candidate last active inside the recent-use window is SPARED — the case where the tab has
 *      been idle for a few hours while somebody else signs in repeatedly. A live session outranks
 *      the cap; the cap exists to stop unbounded accumulation, not to evict somebody mid-sentence.
 *   4. …but only up to the CEILING. Past `terminalLoginTokenMax()` the exemption stops applying and
 *      the least-recently-used go anyway, because every token in a same-day burst is "recent" and
 *      an unconditional exemption would mean nothing is ever revoked. Note what the ordering buys
 *      here: even under ceiling pressure the ACTIVE session is the last thing considered.
 *
 *  The deliberate trade this leaves: between the cap and the ceiling, a burst of sign-ins inside one
 *  window does exceed the cap. That set drains on its own — the first sign-in after those tokens go
 *  quiet revokes them. An over-cap set that converges is worth strictly more than the alternative,
 *  which is what broke the founder.
 *
 *  admin-api stamps `last_used_at` on every `POST /internal/validate`, which is the same oracle
 *  every proxied terminal request and `/api/auth/me` goes through — so "used" here means genuine
 *  traffic, and no new write path was needed to learn it.
 *
 *  NEVER touches differently-named (self-serve) tokens. Every failure (list error, a 404 on a
 *  concurrently-deleted token) is logged and swallowed — a prune problem must never turn a
 *  successful sign-in into a failure (mirrors bootstrapAdminClaim / provisionUserWorkspace above). */
async function pruneLoginTokens(userId: string | number): Promise<void> {
  try {
    const listed = await listUserTokens(userId);
    if (!listed.ok || !listed.data) {
      console.warn(`[terminal-auth] login-token prune skipped (list failed): ${listed.error}`);
      return;
    }
    const cap = terminalLoginTokenCap();
    const max = terminalLoginTokenMax();
    const windowMs = terminalLoginRecentUseWindowMs();
    const now = Date.now();

    const loginTokens = listed.data
      .filter((t) => t.name === TERMINAL_LOGIN_TOKEN_NAME)
      // LEAST-RECENTLY-ACTIVE first; ties (and unparseable timestamps) fall back to numeric id,
      // which is monotonic in admin-api, so the order is always total and deterministic.
      .sort((a, b) => {
        const da = lastActiveAt(a);
        const db = lastActiveAt(b);
        if (da !== db) return da - db;
        return Number(a.id) - Number(b.id);
      });

    const candidates = loginTokens.slice(0, Math.max(0, loginTokens.length - cap));
    // How many of the candidates the ceiling forces out no matter how recently they were used —
    // taken from the least-recently-used end, so an active session is the last one reached.
    const forced = Math.max(0, loginTokens.length - max);
    const overflow = candidates.filter(
      (t, i) => i < forced || now - lastActiveAt(t) >= windowMs,
    );
    const spared = candidates.filter((t) => !overflow.includes(t));

    for (const tok of overflow) {
      const revoked = await revokeToken(tok.id);
      if (!revoked.ok) {
        console.warn(`[terminal-auth] login-token prune: revoke of token ${tok.id} failed (swallowed): ${revoked.error}`);
      }
    }
    if (overflow.length) {
      console.info(`[terminal-auth] login-token prune: user ${userId} over cap ${cap}, revoked ${overflow.length} least-recently-used login token(s)`);
    }
    if (spared.length) {
      console.info(`[terminal-auth] login-token prune: user ${userId} kept ${spared.length} over-cap login token(s) active within ${Math.round(windowMs / 3600_000)}h (live sessions outrank the cap, up to max ${max})`);
    }
  } catch (err) {
    console.warn("[terminal-auth] login-token prune failed (sign-in continues):", (err as Error).message);
  }
}

/** Find the user by email, creating them if they don't exist, then mint an APIToken.
 *  Returns the user + token, or an error with an HTTP-ish status for the caller to surface. */
export async function findOrCreateUserToken(
  email: string,
): Promise<{ ok: true; user: AdminUser; token: string } | { ok: false; status: number; error: string }> {
  const found = await findUserByEmail(email);

  let user: AdminUser | undefined;
  let justCreated = false;
  if (found.ok && found.data) {
    user = found.data;
  } else if (found.notFound) {
    const created = await createUser(email);
    if (!created.ok || !created.data) {
      return { ok: false, status: created.status || 500, error: created.error || "Failed to create user" };
    }
    user = created.data;
    justCreated = true;
  } else {
    return { ok: false, status: found.status || 503, error: found.error || "Failed to look up user" };
  }

  // Mint the login token with a stable `terminal-login` name so it is distinguishable from
  // user-created self-serve tokens and can be bounded (find-or-create used to mint unconditionally
  // with no name and no cap → one live token per sign-in, forever).
  const minted = await mintUserToken(user.id, { scopes: ["bot", "tx", "browser"], name: TERMINAL_LOGIN_TOKEN_NAME });
  if (!minted.ok || !minted.data?.token) {
    return { ok: false, status: minted.status || 500, error: minted.error || "Failed to mint API token" };
  }
  // Bound the user's login tokens to the newest N (best-effort; never blocks sign-in).
  await pruneLoginTokens(user.id);
  // First-run bootstrap: on a fresh instance the FIRST successful sign-in claims the admin role
  // (no-op everywhere else — admin exists, or an allowlist runs the instance). Covers both the
  // direct email login and the OAuth signIn callback, which both land here.
  await bootstrapAdminClaim(user.id);
  // On genuine account creation ("account start"), eagerly provision the user's workspace tiers so the
  // Personal baseline + `_system` exist before their first chat. Best-effort (idempotent + lazy fallback).
  if (justCreated) {
    await provisionUserWorkspace(minted.data.token);
  }
  return { ok: true, user, token: minted.data.token };
}
