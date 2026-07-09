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

  const minted = await createUserToken(user.id);
  if (!minted.ok || !minted.data?.token) {
    return { ok: false, status: minted.status || 500, error: minted.error || "Failed to mint API token" };
  }
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
