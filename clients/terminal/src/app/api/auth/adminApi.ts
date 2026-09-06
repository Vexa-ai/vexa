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

// ── the company-layer setup gate ─────────────────────────────────────────────────
//    Founder ruling, 2026-09-02: "global needs to be setup by admin, it just should not let him
//    start the service before that." A fresh instance serves NOBODY until the admin has written the
//    thin company layer — who the company is, its principles, objectives, structure, and what is
//    missing — into the platform `_global` workspace. Until that exists: only the admin may sign in,
//    the flows engine sends nothing, and the operator verbs refuse.
//
//    admin-api owns the truth and answers it over the SAME internal door `internalRequest()` already
//    uses (VEXA_ADMIN_API_URL + X-Internal-Secret). This module is the terminal's ONE reader of it —
//    no other file may probe those endpoints, so there is exactly one place where the fail-safe
//    direction is decided.

/** The refusal sentence. ONE string, spelled exactly this way, used verbatim by every door that
 *  turns a sign-in away while the gate is up — the JSON login route, the magic-link HTML card, the
 *  OAuth callback. It is exported rather than retyped because a paraphrase in one door and not
 *  another teaches the same person two different things about one instance state; the failure that
 *  prevents is a user who reads "under maintenance" on one screen and "not authorised" on the next
 *  and concludes their account is broken. Never reword it in a caller. */
export const SETUP_GATE_REFUSAL = "This Vexa is being set up by its administrator.";

export type GlobalSetupState = "completed" | "missing";

/** What admin-api says about this instance, in one read. */
export interface InstanceState {
  admin_exists: boolean;
  global_setup: GlobalSetupState;
  /** The company the layer names — null while the gate is up, or when the caller may not see it. */
  company: string | null;
}

/** The whole instance state: has an admin been claimed, has the company layer been written, and
 *  who is the company.
 *
 *  ⚠ THE TWO FIELDS FAIL SAFE IN OPPOSITE DIRECTIONS, and each direction prevents a different
 *  outage. This is the single most confusable thing in this file, so it is spelled out:
 *
 *   • `admin_exists` fails towards TRUE — the pre-existing rule, unchanged. A claim screen that
 *     cannot succeed is a dead end (it invites somebody to become the admin of an instance whose
 *     bootstrap edge is unreachable), so when the probe cannot answer we show plain sign-in.
 *
 *   • `global_setup` fails towards "completed" — that is, towards the gate being DOWN. An
 *     unreachable admin-api must NOT lock every user out of an instance that is working fine. The
 *     other direction turns a transient probe failure into a total sign-in outage, on a screen whose
 *     only content is a sentence the locked-out user can do nothing about.
 *
 *  That is not a hole, because THE TERMINAL IS NOT THE CLOSED HALF OF THIS GATE. The fail-CLOSED
 *  half lives where the irreversible things happen: the flows engine refuses to SEND and agent-api's
 *  operator verbs refuse to act while `_global` is missing. Those two decide with authoritative
 *  state in hand and stop on doubt. The terminal's only job here is to not brick sign-in, so on
 *  doubt it opens the door and lets the enforcing layers say no. */
export async function instanceState(): Promise<InstanceState> {
  const res = await internalRequest<{ admin_exists?: boolean; global_setup?: string; company?: string | null }>(
    "/internal/instance",
    { method: "GET" },
  );
  if (!res.ok || !res.data) {
    // Unreachable or unconfigured probe → both fields to their fail-safe values (see above).
    return { admin_exists: true, global_setup: "completed", company: null };
  }
  return {
    // A configured allowlist IS a set of admins, so it answers the admin question on its own
    // (mirrors instanceHasAdmin below). The probe still runs, because `global_setup` is a separate
    // fact that no allowlist can imply — an allowlist-run instance can absolutely be missing its
    // company layer.
    admin_exists: allowlistConfigured() || res.data.admin_exists === true,
    // Anything that is not literally "missing" (including an older admin-api that does not know the
    // field at all) reads as "completed" — the fail-safe direction, again.
    global_setup: res.data.global_setup === "missing" ? "missing" : "completed",
    company: typeof res.data.company === "string" && res.data.company.trim() ? res.data.company : null,
  };
}

/** MINT THE ADMIN-SETUP SCAFFOLD — the record that makes the setup conversation reachable.
 *
 *  ⚠ WHAT THIS REPLACES, AND WHY IT WAS A HOLE. The hand-off used to live in `localStorage`: the
 *  wizard stashed a pending preset, the workbench opened a chat from it, and the whole existence of
 *  that conversation was one key in one browser. Clear it, or open the instance in a second browser,
 *  and the admin landed in a Personal chat on the generic greeting — "paste a meeting link" — on an
 *  instance that served nobody, with the setup marker already saying "handoff" so nothing re-opened
 *  it. Verified live on 2026-09-02. A conversation the product depends on cannot live in the one
 *  place a person can clear by accident.
 *
 *  The scaffold is that conversation as a SERVER record (PRD §5.5): who it is for, which workspaces
 *  it mounts, which preset opens it, which tabs it shows. The claim mints it and the client follows
 *  the returned `url` — so a second browser, a cleared browser, and a reload all arrive at the SAME
 *  chat, because the id is in the URL and the record is on the server.
 *
 *  Minting is INTERNAL-TIER on agent-api (a scaffold names mounts and composes an opening, so a
 *  caller who could mint one for another address could drive that person's agent). This server
 *  holds that secret; a browser never does. Failure is REPORTED, never swallowed: the caller
 *  decides what to do with a claim that succeeded and a conversation that could not be made.
 */
export interface MintedScaffold { id: string; url: string }

/** WHICH ARRIVAL THIS IS. Two so far, and they are the same mechanism with different records:
 *
 *  · `admin-setup` — the first admin claimed the instance and the company layer is not written yet.
 *  · `first-visit` — anybody signing in with no `?s=` of their own, including an admin whose company
 *    layer is already `completed`. That last case is a rule, not an oversight (founder ruling
 *    2026-09-02, F42): offering the setup conversation again to an instance that IS set up says the
 *    product does not know its own state.
 *
 *  The opening PRESET is named per kind and the body lives in `_global/asks/<opening>.md`, so what
 *  either arrival says is admin-editable and no prompt text is ever composed here. */
export type ArrivalKind = "admin-setup" | "first-visit";

const ARRIVAL_OPENING: Record<ArrivalKind, string> = {
  "admin-setup": "setup-global",
  "first-visit": "first-visit",
};

/** Mint ONE arrival scaffold. The two callers below differ only in the record they ask for. */
async function mintArrivalScaffold(
  kind: ArrivalKind,
  email: string,
  userId: string | number,
  provenance: { flow: string; step: string },
): Promise<AdminResult<MintedScaffold>> {
  const url = (process.env.AGENT_API_URL || "").replace(/\/$/, "");
  const secret = process.env.VEXA_INTERNAL_API_SECRET || "";
  if (!url || !secret) {
    return { ok: false, status: 503, error: "Scaffold minting is not configured (AGENT_API_URL / VEXA_INTERNAL_API_SECRET)" };
  }
  try {
    const res = await fetch(`${url}/internal/scaffolds`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Internal-Secret": secret },
      // `workspaces` is deliberately ABSENT, not `[]`: the server derives the mount set from the
      // address — `_global` + the admin's own desk for a claim, and for a first visit the
      // workspaces already shared with that address plus the meetings it is invited to. Deriving it
      // there keeps one rule in one place rather than two that drift, and it is the half a client
      // could not compute anyway. `tabs`/`focus` likewise come from the preset's own frontmatter.
      body: JSON.stringify({
        who: email,
        kind,
        opening: ARRIVAL_OPENING[kind],
        provenance: { ...provenance, minted_by: String(userId) },
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) {
      const detail = (await res.text().catch(() => "")).slice(0, 500);
      return { ok: false, status: res.status, error: detail || `agent-api returned ${res.status}` };
    }
    return { ok: true, status: res.status, data: (await res.json()) as MintedScaffold };
  } catch (err) {
    const e = err as Error;
    return { ok: false, status: 0, error: e.name === "TimeoutError" ? "scaffold mint timed out" : e.message };
  }
}

/** The admin claim's arrival — the setup conversation. */
export const mintAdminSetupScaffold = (email: string, userId: string | number) =>
  mintArrivalScaffold("admin-setup", email, userId, { flow: "admin-claim", step: "claim-admin" });

/** AN ORDINARY SIGN-IN'S ARRIVAL (F42, founder ruling 2026-09-02).
 *
 *  Signed in as a new user, the founder got a seeded "Personal" chat on the generic greeting, an
 *  ADMIN-ONLY "Organisation setup" card offered to a plain member, and his empty desk's README
 *  template rendered as a page — *"(unset) — this workspace has not been set up yet…"*. His words:
 *  *"i logged as new user, that's what i see - not happy about that."*
 *
 *  Every one of those was the product composing a landing out of whatever happened to be lying
 *  around, which is the exact failure the scaffold exists to end: a sign-in that carries no `?s=`
 *  now MINTS its own arrival, so a first visit is a record — who it is for, which workspaces are
 *  already shared with that address, which meetings it is invited to — rather than a guess.
 *
 *  ⚠ A FAILED MINT MUST NOT COST SOMEBODY THEIR SIGN-IN. It is logged and the visitor lands on `/`
 *  as before. That is the OPPOSITE trade from the admin claim, and deliberately so: there, the role
 *  had already changed by the time the mint ran, so a silent failure would have left a person who IS
 *  the administrator with no way into the conversation that says so — it had to be surfaced. Here
 *  nothing has changed except that they are signed in, which is what they asked for. */
/** WHAT THIS ADDRESS HAS TO COME BACK TO — asked of agent-api, never guessed here.
 *
 *  `has` is true when they hold a chat thread OR a desk past `new`; `probed` says whether we got an
 *  answer at all, so a caller can tell "they are new" from "we could not ask".
 *
 *  Two evidences rather than one because they fail in opposite directions. A person who has talked
 *  to their agent has threads. A person a colleague put in a meeting has a desk with a report in it
 *  and may never have typed a word — and greeting THEM as a stranger is the same defect wearing a
 *  different hat. */
export async function hasHistory(email: string): Promise<{ has: boolean; probed: boolean; why: string }> {
  const url = (process.env.AGENT_API_URL || "").replace(/\/$/, "");
  const secret = process.env.VEXA_INTERNAL_API_SECRET || "";
  if (!url || !secret) return { has: false, probed: false, why: "not configured" };
  try {
    const res = await fetch(`${url}/internal/has-history?who=${encodeURIComponent(email)}`, {
      headers: { "X-Internal-Secret": secret },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return { has: false, probed: false, why: `agent-api returned ${res.status}` };
    const body = (await res.json()) as { has_history?: unknown; sessions?: unknown; desk?: unknown };
    return {
      has: body?.has_history === true,
      probed: true,
      why: `sessions=${String(body?.sessions ?? "?")} desk=${String(body?.desk ?? "?")}`,
    };
  } catch (err) {
    return { has: false, probed: false, why: (err as Error).message };
  }
}

/** A FIRST VISIT IS FOR SOMEBODY WITH NO FIRST VISIT BEHIND THEM (Vexa-ai/vexa#1591).
 *
 *  This used to mint on every sign-in that named no destination, so the admin who had spent a
 *  morning on this instance signed in again and was introduced to the product: *"i logged in again
 *  and now see no chats and it's starting over again while it has the context"*. F42's rule was
 *  right and its condition was wrong — "the link named nowhere" is not the same question as "there
 *  is nowhere to go".
 *
 *  THE GUARD LIVES HERE, not at one door, because all four doors — magic link, direct login, OAuth,
 *  the admin claim on an already-set-up instance — are the same moment and would otherwise drift.
 *
 *  IT FAILS TOWARDS NOT MINTING, and that costs almost nothing: a probe that cannot answer is
 *  talking to the same agent-api the mint itself needs one line later, so an outage lands the person
 *  on `/` either way. What it buys is that a blip can never re-commit the reported defect — a
 *  returning person told, again, that we have never met. */
export async function mintFirstVisitScaffold(
  email: string,
  userId: string | number,
): Promise<AdminResult<MintedScaffold>> {
  const history = await hasHistory(email);
  if (history.has || !history.probed) {
    return {
      ok: false,
      status: 409,
      error: history.has
        ? `no arrival minted — ${email} has history to return to (${history.why})`
        : `no arrival minted — could not ask whether ${email} has history (${history.why})`,
    };
  }
  return mintArrivalScaffold("first-visit", email, userId, { flow: "sign-in", step: "first-visit" });
}

/** Does this instance have an admin yet? An allowlist counts as "yes" (those emails ARE admins),
 *  and short-circuits before the probe — those addresses are admins whatever admin-api thinks.
 *  FAIL-SAFE towards true: if the probe can't answer, the login surface shows plain sign-in
 *  rather than dangling a claim screen that can't succeed. */
export async function instanceHasAdmin(): Promise<boolean> {
  if (allowlistConfigured()) return true;
  return (await instanceState()).admin_exists;
}

/** admin-api's verdict on one address while the gate is up. */
export interface SigninVerdict extends InstanceState {
  allowed: boolean;
  reason: string;
}

/** May THIS address sign in right now?
 *
 *  admin-api answers false ONLY when the gate is up AND an admin already exists AND this is not
 *  that admin. On a virgin instance (no admin claimed yet) the answer is true, because the next
 *  sign-in is the one that claims admin — refusing it would make a fresh instance unclaimable,
 *  which is the exact deadlock the ruling is not asking for.
 *
 *  FAIL-SAFE towards ALLOWED, for the same reason `global_setup` fails towards "completed": the
 *  terminal holds the open half of this gate. A probe that cannot answer must not turn a network
 *  blip into "nobody can log in"; the flows engine and the operator verbs still refuse to act. Note
 *  the deliberate `!== false` below — a malformed body is a probe that could not answer, not a
 *  refusal.
 *
 *  CALL THIS BEFORE `findOrCreateUserToken()`, never after. That function CREATES the user as a
 *  side effect, so checking afterwards leaves a real account behind for somebody who was never
 *  admitted — a ghost row that then looks like a legitimate member of the instance. */
export async function signinAllowed(email: string): Promise<SigninVerdict> {
  const res = await internalRequest<{
    allowed?: boolean; reason?: string; admin_exists?: boolean; global_setup?: string; company?: string | null;
  }>("/internal/signin-allowed", { method: "POST", body: JSON.stringify({ email }) });

  if (!res.ok || !res.data) {
    console.warn(`[terminal-auth] setup-gate probe unavailable, sign-in ALLOWED (fail-safe): ${res.error}`);
    return { allowed: true, reason: "probe-unavailable", admin_exists: true, global_setup: "completed", company: null };
  }
  return {
    allowed: res.data.allowed !== false,
    reason: typeof res.data.reason === "string" ? res.data.reason : "",
    admin_exists: res.data.admin_exists === true,
    global_setup: res.data.global_setup === "missing" ? "missing" : "completed",
    company: typeof res.data.company === "string" && res.data.company.trim() ? res.data.company : null,
  };
}

export type ClaimResult =
  | { ok: true; claimed: boolean }
  | { ok: false; status: number; error: string };

/** Ask admin-api to make this user the instance's administrator.
 *
 *  ⚠ WHY THIS IS EXPORTED, AND WHAT WENT WRONG WITHOUT IT (observed live 2026-09-02, 08:48Z).
 *  The claim used to exist ONLY inside `findOrCreateUserToken`, i.e. only on the path a sign-in
 *  takes. That is fine on a fresh instance, where the first sign-in is imminent — and a dead end on
 *  an instance that already has live sessions predating the gate. The founder's own browser held a
 *  session minted before any of this existed: `admin_exists` was false, his cookie was valid, and
 *  his cookie will never traverse a sign-in door again. So the screen said "not set up" forever and
 *  there was nothing anywhere that could set it up. A role that can only be claimed by an event that
 *  can no longer happen is not a role, it is a deadlock.
 *
 *  Hence a claim reachable by an ALREADY-SIGNED-IN subject (POST /api/auth/claim-admin). Two
 *  properties that route depends on and that live here rather than there:
 *    • admin-api serialises concurrent claims under an advisory lock, so two racing tabs are safe
 *      and the loser simply learns an admin now exists;
 *    • it is a no-op once an admin exists, so this can never TRANSFER the role.
 *
 *  Unlike `bootstrapAdminClaim` below, this REPORTS its outcome — a user who pressed a button that
 *  says "claim this instance" is owed the answer, where a background step on a sign-in was not. */
export async function claimAdminRole(userId: string | number): Promise<ClaimResult> {
  const res = await internalRequest<{ claimed?: boolean }>("/internal/bootstrap-admin", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) return { ok: false, status: res.status || 503, error: res.error || "admin-api refused the claim" };
  return { ok: true, claimed: res.data?.claimed === true };
}

/** Claim the admin role for this user IF the instance has none — the "first sign-in = admin"
 *  step, called on every successful login (admin-api makes it a no-op once an admin exists).
 *  BEST-EFFORT: a failure must never block sign-in; the claim screen simply reappears. */
async function bootstrapAdminClaim(userId: string | number): Promise<void> {
  if (allowlistConfigured()) return; // allowlist-run instance → role claims stay off
  const res = await claimAdminRole(userId);
  if (res.ok && res.claimed) {
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
