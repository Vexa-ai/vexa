import http from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import {
  WebhookDestinationError,
  isBlockedAddress,
  postToWebhookDestination,
  resolveWebhookDestination,
  type Resolver,
  type WebhookDestination,
} from "@/lib/webhook-destination";

const publicResolver: Resolver = async () => [{ address: "93.184.216.34", family: 4 }];
const resolverTo =
  (...addresses: string[]): Resolver =>
  async () =>
    addresses.map((address) => ({ address, family: address.includes(":") ? 6 : 4 }));

async function refusal(url: unknown, resolver: Resolver = publicResolver): Promise<string> {
  try {
    await resolveWebhookDestination(url, resolver);
  } catch (error) {
    expect(error).toBeInstanceOf(WebhookDestinationError);
    return (error as Error).message;
  }
  throw new Error(`expected ${String(url)} to be refused`);
}

describe("isBlockedAddress", () => {
  it.each([
    "127.0.0.1",
    "10.1.2.3",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "255.255.255.255",
    "::1",
    "::",
    "fd00::1",
    "fe80::1",
    "ff02::1",
    "::ffff:127.0.0.1",
    "::ffff:7f00:1",
    "::ffff:a9fe:a9fe",
    "not-an-ip",
  ])("blocks %s", (address) => {
    expect(isBlockedAddress(address)).toBe(true);
  });

  it.each(["93.184.216.34", "8.8.8.8", "172.32.0.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])(
    "allows %s",
    (address) => {
      expect(isBlockedAddress(address)).toBe(false);
    }
  );
});

describe("resolveWebhookDestination", () => {
  it("accepts a public https URL and pins the resolved address", async () => {
    const destination = await resolveWebhookDestination(
      "https://hooks.example.com/vexa?x=1",
      publicResolver
    );
    expect(destination.hostname).toBe("hooks.example.com");
    expect(destination.address).toBe("93.184.216.34");
    expect(destination.url.pathname).toBe("/vexa");
  });

  it("accepts a public http URL, as real deliveries do", async () => {
    const destination = await resolveWebhookDestination("http://hooks.example.com/", publicResolver);
    expect(destination.url.protocol).toBe("http:");
  });

  it("accepts a public literal address without resolving it", async () => {
    const resolver: Resolver = async () => {
      throw new Error("must not resolve a literal address");
    };
    const destination = await resolveWebhookDestination("https://8.8.8.8/hook", resolver);
    expect(destination.address).toBe("8.8.8.8");
  });

  it.each([
    ["ftp://hooks.example.com/", "http or https"],
    ["file:///etc/passwd", "http or https"],
    ["javascript:alert(1)", "http or https"],
    ["not a url", "not a valid URL"],
    ["https://user:pw@hooks.example.com/", "credentials"],
  ])("refuses %s", async (url, message) => {
    expect(await refusal(url)).toContain(message);
  });

  it("refuses a missing URL", async () => {
    expect(await refusal(undefined)).toBe("No webhook URL provided");
    expect(await refusal("  ")).toBe("No webhook URL provided");
  });

  it.each([
    "http://localhost:8080/",
    "http://LOCALHOST./",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://admin-api:8001/admin/users",
    "http://127.0.0.1/",
    "http://0x7f.1/",
    "http://2130706433/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://10.0.0.5/",
  ])("refuses internal target %s", async (url) => {
    expect(await refusal(url)).toBe("Webhook URL cannot target internal or private networks");
  });

  it("refuses a hostname that resolves to an internal address", async () => {
    expect(await refusal("https://rebind.example.com/", resolverTo("169.254.169.254"))).toBe(
      "Webhook URL cannot target internal or private networks"
    );
  });

  it("refuses when any resolved address is internal", async () => {
    expect(
      await refusal("https://mixed.example.com/", resolverTo("93.184.216.34", "10.0.0.1"))
    ).toBe("Webhook URL cannot target internal or private networks");
  });

  it("refuses a hostname that does not resolve", async () => {
    expect(await refusal("https://nowhere.example.com/", resolverTo())).toBe(
      "Webhook URL hostname could not be resolved"
    );
    const failing: Resolver = async () => {
      throw new Error("ENOTFOUND");
    };
    expect(await refusal("https://nowhere.example.com/", failing)).toBe(
      "Webhook URL hostname could not be resolved"
    );
  });
});

describe("postToWebhookDestination", () => {
  let server: http.Server | null = null;

  afterEach(async () => {
    if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = null;
  });

  async function startServer(
    handler: http.RequestListener
  ): Promise<{ port: number; seen: http.IncomingMessage[] }> {
    const seen: http.IncomingMessage[] = [];
    server = http.createServer((req, res) => {
      seen.push(req);
      handler(req, res);
    });
    await new Promise<void>((resolve) => server!.listen(0, "127.0.0.1", () => resolve()));
    return { port: (server!.address() as AddressInfo).port, seen };
  }

  // The test receiver is on loopback, so the destination is built directly
  // (resolveWebhookDestination would rightly refuse it).
  function destinationFor(port: number, path = "/hook?a=1"): WebhookDestination {
    return {
      url: new URL(`http://hooks.example.com:${port}${path}`),
      hostname: "hooks.example.com",
      address: "127.0.0.1",
      family: 4,
    };
  }

  it("connects to the validated address with the original Host header", async () => {
    let body = "";
    const { port, seen } = await startServer((req, res) => {
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        res.writeHead(204);
        res.end();
      });
    });

    const result = await postToWebhookDestination(destinationFor(port), {
      headers: { "Content-Type": "application/json", "X-Webhook-Signature": "sha256=abc" },
      body: '{"event_type":"test"}',
      timeoutMs: 5000,
    });

    expect(result).toEqual({ status: 204, ok: true });
    expect(seen[0].method).toBe("POST");
    expect(seen[0].url).toBe("/hook?a=1");
    expect(seen[0].headers.host).toBe(`hooks.example.com:${port}`);
    expect(seen[0].headers["x-webhook-signature"]).toBe("sha256=abc");
    expect(body).toBe('{"event_type":"test"}');
  });

  it("reports a redirect as its status and does not follow it", async () => {
    const { port, seen } = await startServer((_req, res) => {
      res.writeHead(302, { Location: "http://169.254.169.254/latest/meta-data/" });
      res.end();
    });

    const result = await postToWebhookDestination(destinationFor(port), {
      headers: {},
      body: "{}",
      timeoutMs: 5000,
    });

    expect(result).toEqual({ status: 302, ok: false });
    expect(seen).toHaveLength(1);
  });

  it("reports a non-2xx response as not ok", async () => {
    const { port } = await startServer((_req, res) => {
      res.writeHead(500);
      res.end("boom");
    });
    const result = await postToWebhookDestination(destinationFor(port), {
      headers: {},
      body: "{}",
      timeoutMs: 5000,
    });
    expect(result).toEqual({ status: 500, ok: false });
  });

  it("times out with a message containing 'timeout'", async () => {
    const { port } = await startServer(() => {
      /* never respond */
    });
    await expect(
      postToWebhookDestination(destinationFor(port), { headers: {}, body: "{}", timeoutMs: 100 })
    ).rejects.toThrow(/timeout/);
  });
});
