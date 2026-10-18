/** How often the emailed sign-in link may be asked for: per client address and per email address.
 *
 *  Each request for a link costs an admin-api round-trip and, for an admitted address, a mail. Two
 *  sliding windows bound that: `MAGIC_LINK_RATE_PER_IP` requests per client address (default 20) and
 *  `MAGIC_LINK_RATE_PER_ADDRESS` per email address (default 5), each over
 *  `MAGIC_LINK_RATE_WINDOW_SECONDS` (default 900). The client address is the one `server.mjs` stamps
 *  (`./clientAddress.mjs`).
 *
 *  ⚠ PROCESS-LOCAL: each terminal replica keeps its own counts, so with N replicas a client gets up
 *  to N times each limit. (The record of which links were USED is not local: admin-api keeps it in
 *  the service Redis — `magicToken.ts`, `redeemMagicToken`.) The map is swept as it is used and
 *  capped, so it cannot grow without bound.
 */

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
export function takeLinkRequest(client: string, email: string, now: number = Date.now()): LinkRateVerdict {
  const windowMs = setting("MAGIC_LINK_RATE_WINDOW_SECONDS", 900) * 1000;
  if (!take(`ip:${client || "unknown"}`, setting("MAGIC_LINK_RATE_PER_IP", 20), windowMs, now)) return "client-limited";
  if (!take(`to:${email}`, setting("MAGIC_LINK_RATE_PER_ADDRESS", 5), windowMs, now)) return "address-limited";
  return "ok";
}

/** Test seam — forgets every count. Never called by the routes. */
export function _resetLinkRateLimits(): void {
  hits.clear();
}
