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

const blockedAddresses = new BlockList();
for (const [network, prefix] of [
  ["0.0.0.0", 8], // "this" network
  ["10.0.0.0", 8], // private
  ["100.64.0.0", 10], // carrier-grade NAT
  ["127.0.0.0", 8], // loopback
  ["169.254.0.0", 16], // link-local, including cloud metadata
  ["172.16.0.0", 12], // private
  ["192.0.0.0", 24], // IETF protocol assignments
  ["192.168.0.0", 16], // private
  ["198.18.0.0", 15], // benchmarking
  ["224.0.0.0", 4], // multicast
  ["240.0.0.0", 4], // reserved, including broadcast
] as const) {
  blockedAddresses.addSubnet(network, prefix, "ipv4");
}
for (const [network, prefix] of [
  ["::", 128], // unspecified
  ["::1", 128], // loopback
  ["64:ff9b::", 96], // NAT64
  ["fc00::", 7], // unique local
  ["fe80::", 10], // link-local
  ["ff00::", 8], // multicast
] as const) {
  blockedAddresses.addSubnet(network, prefix, "ipv6");
}

/** IPv4 address embedded in an IPv4-mapped or IPv4-compatible IPv6 address. */
function embeddedIpv4(address: string): string | null {
  const lower = address.toLowerCase();
  const match = /^::(?:ffff:)?(\d{1,3}(?:\.\d{1,3}){3})$/.exec(lower);
  if (match) return match[1];
  const hex = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(lower);
  if (hex) {
    const high = parseInt(hex[1], 16);
    const low = parseInt(hex[2], 16);
    return [high >> 8, high & 0xff, low >> 8, low & 0xff].join(".");
  }
  return null;
}

export function isBlockedAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) return blockedAddresses.check(address, "ipv4");
  if (family === 6) {
    const v4 = embeddedIpv4(address);
    if (v4 !== null) return isIP(v4) !== 4 || blockedAddresses.check(v4, "ipv4");
    return blockedAddresses.check(address, "ipv6");
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
