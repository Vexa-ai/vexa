/** Asking for the emailed sign-in link is rate-limited per client address and per email address.
 *
 *  Over the client limit the route answers 429 before admin-api is asked; over the address limit it
 *  answers the same 200 as always and sends nothing, so the limit tells nobody anything about the
 *  address. The client address is the one server.mjs stamps from the TCP peer, trusting
 *  X-Forwarded-For only from a proxy.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MailArgs = { to: string; subject: string; text: string };
const sendMail = vi.fn(async (_opts: MailArgs): Promise<void> => {});
vi.mock("../mailer", () => ({ sendMail: (opts: MailArgs) => sendMail(opts) }));

import { POST as requestLinkRoute } from "../request-link/route";
import { _settleLinkDeliveries } from "../linkDelivery";
import { _resetLinkRateLimits, takeLinkRequest } from "../linkRateLimit";
import { CLIENT_ADDRESS_HEADER, clientAddress, isPrivateAddress, stampClientAddress, trustedProxies } from "../clientAddress.mjs";

const admission = vi.fn(async (url: string) =>
  String(url).includes("/internal/signin-admission")
    ? new Response(JSON.stringify({ admitted: true, why: "allow-list" }), { status: 200 })
    : new Response("nope", { status: 500 }));

async function ask(email: string, client = "203.0.113.7") {
  const req = {
    json: async () => ({ email }),
    headers: new Headers({ host: "terminal.test", [CLIENT_ADDRESS_HEADER]: client }),
  } as unknown as import("next/server").NextRequest;
  const res = await requestLinkRoute(req);
  await _settleLinkDeliveries();
  return res;
}

beforeEach(() => {
  _resetLinkRateLimits();
  sendMail.mockClear();
  admission.mockClear();
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret-0123456789abcdef");
  vi.stubEnv("NEXTAUTH_URL", "https://terminal.test");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubGlobal("fetch", admission);
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("per email address", () => {
  it("sends at most five links to one address per window, and answers the same 200 after that", async () => {
    for (let i = 0; i < 5; i++) expect((await ask("ana@example.com", `198.51.100.${i}`)).status).toBe(200);
    expect(sendMail).toHaveBeenCalledTimes(5);
    const sixth = await ask("ana@example.com", "198.51.100.99");
    expect(sixth.status).toBe(200);
    expect(await sixth.json()).toEqual({ ok: true });
    expect(sendMail).toHaveBeenCalledTimes(5);
    expect(admission).toHaveBeenCalledTimes(5);
  });

  it("counts each address on its own", async () => {
    for (let i = 0; i < 5; i++) await ask("ana@example.com", `198.51.100.${i}`);
    await ask("bo@example.com", "198.51.100.50");
    expect(sendMail.mock.calls.map((c) => c[0].to)).toContain("bo@example.com");
  });
});

describe("per client address", () => {
  it("answers 429 past twenty requests from one client, before admin-api is asked", async () => {
    for (let i = 0; i < 20; i++) expect((await ask(`user${i}@example.com`)).status).toBe(200);
    admission.mockClear();
    sendMail.mockClear();
    const over = await ask("user20@example.com");
    expect(over.status).toBe(429);
    expect(over.headers.get("retry-after")).toBeTruthy();
    expect(admission).not.toHaveBeenCalled();
    expect(sendMail).not.toHaveBeenCalled();
    expect((await ask("user21@example.com", "203.0.113.8")).status).toBe(200);
  });

  it("the limits and the window can be set", () => {
    vi.stubEnv("MAGIC_LINK_RATE_PER_IP", "2");
    vi.stubEnv("MAGIC_LINK_RATE_WINDOW_SECONDS", "60");
    const t = 1_700_000_000_000;
    expect(takeLinkRequest("c", "a@x.io", t)).toBe("ok");
    expect(takeLinkRequest("c", "b@x.io", t)).toBe("ok");
    expect(takeLinkRequest("c", "c@x.io", t)).toBe("client-limited");
    expect(takeLinkRequest("c", "d@x.io", t + 61_000)).toBe("ok");
  });
});

describe("the client address", () => {
  it("is the TCP peer, and a public peer's X-Forwarded-For is ignored", () => {
    expect(clientAddress("203.0.113.7", "198.51.100.1")).toBe("203.0.113.7");
    expect(clientAddress("::ffff:203.0.113.7", undefined)).toBe("203.0.113.7");
    expect(clientAddress(undefined, "198.51.100.1")).toBe("unknown");
  });

  it("behind a private or named proxy, is the rightmost X-Forwarded-For entry", () => {
    for (const proxy of ["127.0.0.1", "10.1.2.3", "172.18.0.1", "192.168.1.1", "::1", "fd00::1"]) {
      expect(isPrivateAddress(proxy)).toBe(true);
      expect(clientAddress(proxy, "198.51.100.1, 203.0.113.9")).toBe("203.0.113.9");
    }
    expect(clientAddress("203.0.113.50", "198.51.100.1", trustedProxies({ TERMINAL_TRUSTED_PROXIES: "203.0.113.50" })))
      .toBe("198.51.100.1");
    expect(isPrivateAddress("172.32.0.1")).toBe(false);
    expect(isPrivateAddress("8.8.8.8")).toBe(false);
  });

  it("server.mjs's stamp replaces any inbound copy of the header", () => {
    const req = { headers: { [CLIENT_ADDRESS_HEADER]: "1.1.1.1", "x-forwarded-for": "9.9.9.9" } as Record<string, unknown>,
                  socket: { remoteAddress: "203.0.113.7" } };
    stampClientAddress(req, []);
    expect(req.headers[CLIENT_ADDRESS_HEADER]).toBe("203.0.113.7");
  });
});
