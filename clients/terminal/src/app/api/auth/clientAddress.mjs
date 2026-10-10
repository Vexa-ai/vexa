/** The client address the terminal's routes may trust, stamped onto each request by `server.mjs`.
 *
 *  The TCP peer cannot be forged; `X-Forwarded-For` can, by anyone. So a peer's `X-Forwarded-For` is
 *  believed only when the peer is a proxy this deployment NAMED: a loopback address (a proxy in the
 *  same network namespace), or an address or CIDR range listed in `TERMINAL_TRUSTED_PROXIES`. Then the
 *  entry that proxy appended (the rightmost) is the client. Any other peer's `X-Forwarded-For` is
 *  ignored — a private peer included, because a private peer is not necessarily a proxy that appends
 *  the header: a port forwarder, an L4 balancer or a neighbour on the same network passes the caller's
 *  own header through. Any inbound copy of the stamped header is overwritten, so a route reading it
 *  reads only what this module decided.
 */

/** The header `server.mjs` stamps; routes read it (e.g. the sign-in link's per-IP limit). */
export const CLIENT_ADDRESS_HEADER = "x-vexa-client-address";

/** @param {unknown} ip @returns {string} */
function normalize(ip) {
  const s = typeof ip === "string" ? ip.trim().toLowerCase() : "";
  return s.startsWith("::ffff:") && s.includes(".") ? s.slice(7) : s;
}

/** Whether `ip` is loopback, private, link-local, carrier-grade NAT or unique-local. Informational:
 *  being private does NOT make a peer trusted (see the header).
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

/** Whether `ip` is a loopback address (127.0.0.0/8, ::1).
 *  @param {string} ip @returns {boolean} */
export function isLoopback(ip) {
  const a = normalize(ip);
  return a === "::1" || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(a);
}

/** An address as [family, BigInt], or null.  @param {string} ip @returns {[4|6, bigint] | null} */
function toBits(ip) {
  const a = normalize(ip);
  const v4 = a.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const parts = v4.slice(1).map(Number);
    if (parts.some((n) => n > 255)) return null;
    return [4, parts.reduce((acc, n) => (acc << 8n) | BigInt(n), 0n)];
  }
  if (!/^[0-9a-f:]+$/.test(a) || !a.includes(":")) return null;
  const halves = a.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
  if (fill < 0 || (halves.length === 1 && head.length !== 8)) return null;
  const groups = [...head, ...Array(fill).fill("0"), ...tail];
  if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  return [6, groups.reduce((acc, g) => (acc << 16n) | BigInt(parseInt(g, 16)), 0n)];
}

/** Whether `ip` is `entry` — one address, or a CIDR range (`10.0.0.0/8`, `fd00::/8`).
 *  @param {string} ip @param {string} entry @returns {boolean} */
export function matchesProxy(ip, entry) {
  const [base, lenRaw] = String(entry).trim().split("/");
  const addr = toBits(ip), net = toBits(base || "");
  if (!addr || !net || addr[0] !== net[0]) return false;
  const width = addr[0] === 4 ? 32 : 128;
  const len = lenRaw === undefined ? width : Number(lenRaw);
  if (!Number.isInteger(len) || len < 0 || len > width) return false;
  const shift = BigInt(width - len);
  return (addr[1] >> shift) === (net[1] >> shift);
}

/** The client address for a request from `peer` carrying `forwardedFor`.
 *  @param {unknown} peer @param {unknown} forwardedFor @param {string[]} [trusted]
 *  @returns {string} */
export function clientAddress(peer, forwardedFor, trusted = []) {
  const p = normalize(peer);
  if (p && (isLoopback(p) || trusted.some((entry) => matchesProxy(p, entry)))) {
    const hops = String(Array.isArray(forwardedFor) ? forwardedFor.join(",") : forwardedFor || "")
      .split(",").map((h) => h.trim()).filter(Boolean);
    if (hops.length) return normalize(hops[hops.length - 1]);
  }
  return p || "unknown";
}

/** The proxies `TERMINAL_TRUSTED_PROXIES` names: addresses or CIDR ranges, comma-separated.
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
