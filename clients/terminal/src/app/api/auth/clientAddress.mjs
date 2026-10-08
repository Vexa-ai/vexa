/** The client address the terminal's routes may trust, stamped onto each request by `server.mjs`.
 *
 *  The TCP peer cannot be forged. When the peer is a proxy — a loopback, private, link-local or
 *  unique-local address (a container network, a host reverse proxy), or one named in
 *  `TERMINAL_TRUSTED_PROXIES` — the address it appended to `X-Forwarded-For` (the rightmost entry)
 *  is the client. A public peer's `X-Forwarded-For` is ignored. Any inbound copy of the stamped
 *  header is overwritten, so a route reading it reads only what this module decided.
 */

/** The header `server.mjs` stamps; routes read it (e.g. the sign-in link's per-IP limit). */
export const CLIENT_ADDRESS_HEADER = "x-vexa-client-address";

/** @param {unknown} ip @returns {string} */
function normalize(ip) {
  const s = typeof ip === "string" ? ip.trim().toLowerCase() : "";
  return s.startsWith("::ffff:") && s.includes(".") ? s.slice(7) : s;
}

/** Whether `ip` is loopback, private, link-local, carrier-grade NAT or unique-local.
 *  @param {string} ip @returns {boolean} */
export function isPrivateAddress(ip) {
  const a = normalize(ip);
  const v4 = a.match(/^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/);
  if (v4) {
    const [x, y] = [Number(v4[1]), Number(v4[2])];
    return x === 10 || x === 127 || (x === 172 && y >= 16 && y <= 31) || (x === 192 && y === 168)
      || (x === 169 && y === 254) || (x === 100 && y >= 64 && y <= 127);
  }
  return a === "::1" || /^f[cd][0-9a-f]{2}:/.test(a) || /^fe[89ab][0-9a-f]:/.test(a);
}

/** The client address for a request from `peer` carrying `forwardedFor`.
 *  @param {unknown} peer @param {unknown} forwardedFor @param {string[]} [trusted]
 *  @returns {string} */
export function clientAddress(peer, forwardedFor, trusted = []) {
  const p = normalize(peer);
  if (p && (isPrivateAddress(p) || trusted.map(normalize).includes(p))) {
    const hops = String(Array.isArray(forwardedFor) ? forwardedFor.join(",") : forwardedFor || "")
      .split(",").map((h) => h.trim()).filter(Boolean);
    if (hops.length) return normalize(hops[hops.length - 1]);
  }
  return p || "unknown";
}

/** The proxies `TERMINAL_TRUSTED_PROXIES` names, comma-separated.
 *  @param {Record<string, string | undefined>} env @returns {string[]} */
export function trustedProxies(env) {
  return String(env.TERMINAL_TRUSTED_PROXIES || "").split(",").map((s) => s.trim()).filter(Boolean);
}

/** Stamp the trusted client address onto a Node request, replacing any inbound copy.
 *  @param {{ headers: Record<string, unknown>, socket?: { remoteAddress?: string } }} req
 *  @param {string[]} trusted */
export function stampClientAddress(req, trusted) {
  req.headers[CLIENT_ADDRESS_HEADER] = clientAddress(req.socket?.remoteAddress, req.headers["x-forwarded-for"], trusted);
}
