/** How often the emailed sign-in link may be asked for: per client address and per email address.
 *
 *  Each request for a link costs an admin-api round-trip and, for an admitted address, a mail. Two
 *  sliding windows bound that: `MAGIC_LINK_RATE_PER_IP` requests per client address (default 20) and
 *  `MAGIC_LINK_RATE_PER_ADDRESS` per email address (default 5), each over
 *  `MAGIC_LINK_RATE_WINDOW_SECONDS` (default 900). The client address is the one `server.mjs` stamps
 *  (`./clientAddress.mjs`).
 *
 *  SHARED SINCE 0.13.4 (M6, security review: "rate limits and locks are process-local"):
 *  `takeLinkRequest` counts in admin-api's service Redis (`POST /internal/rate/take`), so N replicas
 *  give ONE limit. When that store cannot answer, the process-local count below decides instead —
 *  the pre-0.13.4 behaviour — so a Redis outage never closes the sign-in door. The local map is swept
 *  as it is used and capped, so it cannot grow without bound. The invite preview and the join POST,
 *  which had no limit at all, are counted the same way (`takeJoinRequest`).
 */
import { rateTake } from "./adminApi";

const MAX_KEYS = 50_000;

function setting(name: string, fallback: number): number {
  const raw = parseInt(process.env[name] || "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : fallback;
}

const hits = new Map<string, number[]>();

function take(key: string, limit: number, windowMs: number, now: number): boolean {
  const recent = (hits.get(key) || []).filter((t) => t > now - windowMs);
  if (recent.length >= limit) {
    hits.set(key, recent);
    return false;
  }
  recent.push(now);
  hits.delete(key); // re-insert so the map's order is least-recently-used first
  hits.set(key, recent);
  while (hits.size > MAX_KEYS) {
    const oldest = hits.keys().next().value;
    if (oldest === undefined) break;
    hits.delete(oldest);
  }
  return true;
}

export type LinkRateVerdict = "ok" | "client-limited" | "address-limited";

/** Count one request for a link to `email` from `client`, and say whether it may proceed. A request
 *  over the client limit is not counted against the address. */
export function takeLinkRequestLocal(client: string, email: string, now: number = Date.now()): LinkRateVerdict {
  const windowMs = setting("MAGIC_LINK_RATE_WINDOW_SECONDS", 900) * 1000;
  if (!take(`ip:${client || "unknown"}`, setting("MAGIC_LINK_RATE_PER_IP", 20), windowMs, now)) return "client-limited";
  if (!take(`to:${email}`, setting("MAGIC_LINK_RATE_PER_ADDRESS", 5), windowMs, now)) return "address-limited";
  return "ok";
}

/** A rate key's subject, reduced to what signin.v1 `RateTakeRequest.key` admits. */
function subject(v: string): string {
  return (v || "unknown").replace(/[^A-Za-z0-9._@:+-]/g, "_").slice(0, 200) || "unknown";
}

/** The shared count first (one limit for every replica); the local count when it cannot answer. */
export async function takeLinkRequest(client: string, email: string, now: number = Date.now()): Promise<LinkRateVerdict> {
  const window = setting("MAGIC_LINK_RATE_WINDOW_SECONDS", 900);
  const ip = await rateTake(`link-ip:${subject(client)}`, setting("MAGIC_LINK_RATE_PER_IP", 20), window);
  if (ip === "unavailable") return takeLinkRequestLocal(client, email, now);
  if (ip === "limited") return "client-limited";
  const to = await rateTake(`link-to:${subject(email)}`, setting("MAGIC_LINK_RATE_PER_ADDRESS", 5), window);
  if (to === "unavailable") return takeLinkRequestLocal(client, email, now);
  return to === "limited" ? "address-limited" : "ok";
}

/** The invite preview and the join POST (PRD 0.13.4 M6): `JOIN_RATE_PER_IP` (default 60) per client
 *  address per `JOIN_RATE_WINDOW_SECONDS` (default 900). True when the request may proceed. */
export async function takeJoinRequest(client: string, now: number = Date.now()): Promise<boolean> {
  const window = setting("JOIN_RATE_WINDOW_SECONDS", 900);
  const limit = setting("JOIN_RATE_PER_IP", 60);
  const shared = await rateTake(`join-ip:${subject(client)}`, limit, window);
  if (shared !== "unavailable") return shared === "allowed";
  return take(`join:${client || "unknown"}`, limit, window * 1000, now);
}

/** Test seam — forgets every count. Never called by the routes. */
export function _resetLinkRateLimits(): void {
  hits.clear();
}
