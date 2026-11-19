/**
 * Outbound webhook destinations: validation and delivery.
 *
 * Mirrors the meeting-api webhook guard (`meeting_api/webhooks/ssrf.py`) so a
 * test delivery from the dashboard is accepted or refused exactly where a real
 * delivery would be:
 *
 *   - http and https only;
 *   - internal service hostnames and cloud metadata names are refused;
 *   - every address the hostname resolves to must be public;
 *   - the connection is made to the address that was validated (no second DNS
 *     lookup), with the original Host header and TLS server name;
 *   - redirects are not followed.
 */
import { BlockList, isIP } from "node:net";
import { lookup as dnsLookup } from "node:dns/promises";
import http from "node:http";
import https from "node:https";

export class WebhookDestinationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebhookDestinationError";
  }
}

const INTERNAL_TARGET_MESSAGE = "Webhook URL cannot target internal or private networks";

const BLOCKED_HOSTNAMES = new Set([
  "localhost",
  "metadata.google.internal",
  "metadata.amazonaws.com",
  "metadata",
  "api-gateway",
  "admin-api",
  "meeting-api",
  "runtime-api",
  "transcription-collector",
  "redis",
  "postgres",
  "mcp",
]);

// The same lists and the same carried-address rules as the outbound URL guard the services share
// (core/agent/shared/ssrf.py): an IPv6 address is judged by every IPv4 address a socket dialled at
// it would reach, and then by its own range.
const blockedV4 = new BlockList();
for (const [network, prefix] of [
  ["0.0.0.0", 8], // "this" network
  ["10.0.0.0", 8], // private
  ["100.64.0.0", 10], // carrier-grade NAT
  ["127.0.0.0", 8], // loopback
  ["169.254.0.0", 16], // link-local, including cloud metadata
  ["172.16.0.0", 12], // private
  ["192.0.0.0", 24], // IETF protocol assignments
  ["192.0.2.0", 24], // documentation
  ["192.88.99.0", 24], // 6to4 relay anycast
  ["192.168.0.0", 16], // private
  ["198.18.0.0", 15], // benchmarking
  ["198.51.100.0", 24], // documentation
  ["203.0.113.0", 24], // documentation
  ["224.0.0.0", 4], // multicast
  ["240.0.0.0", 4], // reserved, including broadcast
] as const) {
  blockedV4.addSubnet(network, prefix, "ipv4");
}
const blockedV6 = new BlockList();
for (const [network, prefix] of [
  ["::", 128], // unspecified
  ["::1", 128], // loopback
  ["64:ff9b:1::", 48], // local-use IPv4/IPv6 translation
  ["100::", 64], // discard-only
  ["2001::", 23], // IETF protocol assignments, including Teredo 2001::/32
  ["2001:db8::", 32], // documentation
  ["3fff::", 20], // documentation
  ["5f00::", 16], // segment routing SIDs
  ["fc00::", 7], // unique local
  ["fe80::", 10], // link-local
  ["fec0::", 10], // site-local
  ["ff00::", 8], // multicast
] as const) {
  blockedV6.addSubnet(network, prefix, "ipv6");
}
const ISATAP_IDS = ["0000:5efe", "0100:5efe", "0200:5efe", "0300:5efe"];

/** An IPv6 address as its eight 16-bit groups. */
function ipv6Groups(address: string): number[] | null {
  let text = address.toLowerCase().split("%")[0];
  const dotted = /(\d{1,3}(?:\.\d{1,3}){3})$/.exec(text);
  if (dotted) {
    if (isIP(dotted[1]) !== 4) return null;
    const [a, b, c, d] = dotted[1].split(".").map(Number);
    text = text.slice(0, -dotted[1].length) + ((a << 8) | b).toString(16) + ":" + ((c << 8) | d).toString(16);
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
  const groups = [...head, ...Array(Math.max(fill, 0)).fill("0"), ...tail];
  if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  return groups.map((g) => parseInt(g, 16));
}

/** Two 16-bit groups as a dotted IPv4 address. */
function v4(high: number, low: number): string {
  return [high >> 8, high & 0xff, low >> 8, low & 0xff].join(".");
}

const hex4 = (g: number) => g.toString(16).padStart(4, "0");

/** Every IPv4 address an IPv6 address carries: what a socket dialled at it would reach. */
function carriedIpv4(g: number[]): string[] {
  const out: string[] = [];
  const low = v4(g[6], g[7]);
  const zero = (from: number, to: number) => g.slice(from, to).every((x) => x === 0);
  if (zero(0, 5) && g[5] === 0xffff) out.push(low); // mapped ::ffff:a.b.c.d
  else if (zero(0, 4) && g[4] === 0xffff && g[5] === 0) out.push(low); // translated ::ffff:0:a.b.c.d
  else if (zero(0, 6) && (g[6] !== 0 || g[7] > 1)) out.push(low); // compatible ::a.b.c.d
  if (g[0] === 0x64 && g[1] === 0xff9b && zero(2, 6)) out.push(low); // NAT64 64:ff9b::/96
  if (g[0] === 0x2002) out.push(v4(g[1], g[2])); // 6to4
  if (g[0] === 0x2001 && g[1] === 0) {
    out.push(v4(g[2], g[3])); // Teredo server
    out.push(v4(g[6] ^ 0xffff, g[7] ^ 0xffff)); // Teredo client
  }
  if (ISATAP_IDS.includes(`${hex4(g[4])}:${hex4(g[5])}`)) out.push(low);
  return out;
}

export function isBlockedAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) return blockedV4.check(address, "ipv4");
  if (family === 6) {
    const groups = ipv6Groups(address);
    if (groups === null) return true;
    const carried = carriedIpv4(groups);
    if (carried.some((a) => blockedV4.check(a, "ipv4"))) return true;
    if (blockedV6.check(address, "ipv6")) return true;
    return groups[0] >> 8 === 0 && carried.length === 0; // the rest of ::/8 is reserved
  }
  return true; // not an IP address at all
}

export type ResolvedAddress = { address: string; family: 4 | 6 };
export type Resolver = (hostname: string) => Promise<ResolvedAddress[]>;

const systemResolver: Resolver = async (hostname) => {
  const results = await dnsLookup(hostname, { all: true, verbatim: true });
  return results.map((r) => ({ address: r.address, family: r.family === 6 ? 6 : 4 }));
};

export type WebhookDestination = {
  url: URL;
  /** Hostname without IPv6 brackets. */
  hostname: string;
  /** The validated address the request will be sent to. */
  address: string;
  family: 4 | 6;
};

/**
 * Parse and validate a webhook URL, resolving its hostname once.
 * Throws `WebhookDestinationError` with a user-facing message when refused.
 */
export async function resolveWebhookDestination(
  rawUrl: unknown,
  resolver: Resolver = systemResolver
): Promise<WebhookDestination> {
  if (typeof rawUrl !== "string" || rawUrl.trim() === "") {
    throw new WebhookDestinationError("No webhook URL provided");
  }

  let url: URL;
  try {
    url = new URL(rawUrl.trim());
  } catch {
    throw new WebhookDestinationError("Webhook URL is not a valid URL");
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new WebhookDestinationError("Webhook URL must use http or https scheme");
  }
  if (url.username || url.password) {
    throw new WebhookDestinationError("Webhook URL must not contain credentials");
  }

  const hostname = url.hostname.replace(/^\[/, "").replace(/\]$/, "").replace(/\.$/, "").toLowerCase();
  if (!hostname) {
    throw new WebhookDestinationError("Webhook URL must have a valid hostname");
  }
  if (BLOCKED_HOSTNAMES.has(hostname)) {
    throw new WebhookDestinationError(INTERNAL_TARGET_MESSAGE);
  }

  const literalFamily = isIP(hostname);
  if (literalFamily !== 0) {
    if (isBlockedAddress(hostname)) {
      throw new WebhookDestinationError(INTERNAL_TARGET_MESSAGE);
    }
    return { url, hostname, address: hostname, family: literalFamily === 6 ? 6 : 4 };
  }

  let addresses: ResolvedAddress[];
  try {
    addresses = await resolver(hostname);
  } catch {
    addresses = [];
  }
  if (addresses.length === 0) {
    throw new WebhookDestinationError("Webhook URL hostname could not be resolved");
  }
  // Refuse if ANY resolved address is internal, so a mixed answer cannot be
  // used to reach an internal address on a later connection.
  if (addresses.some((a) => isBlockedAddress(a.address))) {
    throw new WebhookDestinationError(INTERNAL_TARGET_MESSAGE);
  }

  return { url, hostname, address: addresses[0].address, family: addresses[0].family };
}

export type WebhookDeliveryResult = { status: number; ok: boolean };

const MAX_RESPONSE_BYTES = 64 * 1024;

/**
 * POST `body` to a validated destination. Connects to `destination.address`
 * only; redirects are reported as their status code, never followed.
 */
export function postToWebhookDestination(
  destination: WebhookDestination,
  {
    headers,
    body,
    timeoutMs,
  }: { headers: Record<string, string>; body: string; timeoutMs: number }
): Promise<WebhookDeliveryResult> {
  const { url, hostname, address, family } = destination;
  const isHttps = url.protocol === "https:";
  const transport = isHttps ? https : http;
  const payload = Buffer.from(body, "utf8");

  return new Promise((resolve, reject) => {
    const req = transport.request({
      host: address,
      family,
      port: url.port ? Number(url.port) : isHttps ? 443 : 80,
      path: `${url.pathname}${url.search}`,
      method: "POST",
      headers: {
        ...headers,
        Host: url.host,
        "Content-Length": String(payload.length),
      },
      // TLS: present and verify the original hostname, not the address.
      ...(isHttps && isIP(hostname) === 0 ? { servername: hostname } : {}),
      agent: false,
    });

    const timer = setTimeout(() => {
      req.destroy(new Error("Webhook request timeout"));
    }, timeoutMs);

    req.on("response", (res) => {
      let received = 0;
      res.on("data", (chunk: Buffer) => {
        received += chunk.length;
        if (received > MAX_RESPONSE_BYTES) res.destroy();
      });
      const finish = () => {
        clearTimeout(timer);
        const status = res.statusCode ?? 0;
        resolve({ status, ok: status >= 200 && status < 300 });
      };
      res.on("end", finish);
      res.on("close", finish);
      res.on("error", finish);
    });
    req.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });

    req.end(payload);
  });
}
