/** /api/auth/request-link — the FRONT half of the magic-link door.
 *
 *  The properties under test are the ones an attacker probes: the response never distinguishes a
 *  known address from an unknown one (or a working mailer from a broken one), and the link that
 *  goes out is a redeem URL carrying a signed token plus the caller's deeplink — reduced to a
 *  site-relative path before it is ever written into a mail.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MailArgs = { to: string; subject: string; text: string };
/** The mailer is the only side effect this route has; the factory closes over the spy (rather than
 *  referencing it eagerly) so vi.mock's hoisting above the const is harmless. */
const sendMail = vi.fn(async (_opts: MailArgs): Promise<void> => {});
vi.mock("../mailer", () => ({ sendMail: (opts: MailArgs) => sendMail(opts) }));

import { POST as requestLinkRoute } from "../request-link/route";
import { _settleLinkDeliveries } from "../linkDelivery";
import { verifyMagicToken } from "../magicToken";

/** The route answers BEFORE it asks admin-api or mails anything (Vexa-ai/vexa#1783 — so a refused
 *  address cannot be told apart by timing), so every case here waits for the delivery it started. */
async function requestLink(req: import("next/server").NextRequest) {
  const res = await requestLinkRoute(req);
  await _settleLinkDeliveries();
  return res;
}

function makeReq(body: unknown, headers: Record<string, string> = { host: "terminal.test" }) {
  return {
    json: async () => body,
    headers: new Headers(headers),
  } as unknown as import("next/server").NextRequest;
}

/** The one mail the route sent, parsed back into { url, token, next }. */
function sentLink() {
  expect(sendMail).toHaveBeenCalledTimes(1);
  const arg = sendMail.mock.calls[0][0];
  const match = /(https?:\/\/\S+)/.exec(arg.text);
  expect(match, `no link in mail body: ${arg.text}`).toBeTruthy();
  const url = new URL(match![1]);
  return { to: arg.to, subject: arg.subject, text: arg.text, url, token: url.searchParams.get("t") || "", next: url.searchParams.get("next") };
}

beforeEach(() => {
  sendMail.mockClear();
  sendMail.mockImplementation(async () => {});
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret-0123456789abcdef");
  vi.stubEnv("NEXTAUTH_URL", "https://terminal.test");
  vi.stubEnv("MAGIC_LINK_TTL_SECONDS", "");
  // admin-api admits every address these cases use; the refusals are in signinAllowList.test.ts.
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubGlobal("fetch", vi.fn(async (url: string) =>
    String(url).includes("/internal/signin-admission")
      ? new Response(JSON.stringify({ admitted: true, why: "allow-list" }), { status: 200 })
      : new Response("nope", { status: 500 })));
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("the link that goes out", () => {
  it("mails a redeem URL whose token verifies for the requested address", async () => {
    const res = await requestLink(makeReq({ email: "Magic-Test@Vexa.ai", next: "/?ask=catch-up" }));
    expect(res.status).toBe(200);

    const link = sentLink();
    expect(link.to).toBe("magic-test@vexa.ai"); // normalised
    expect(link.url.origin).toBe("https://terminal.test");
    expect(link.url.pathname).toBe("/api/auth/redeem");
    expect(link.next).toBe("/?ask=catch-up"); // the deeplink survives the round-trip
    expect(verifyMagicToken(link.token)).toMatchObject({ ok: true, email: "magic-test@vexa.ai" });
    expect(link.text).toContain("expires in 15 minutes");
  });

  it("reduces a hostile next= to / BEFORE it is written into the mail", async () => {
    await requestLink(makeReq({ email: "someone@example.com", next: "https://evil.example/steal" }));
    expect(sentLink().next).toBe("/");
  });

  it("defaults next to / when the caller sends none", async () => {
    await requestLink(makeReq({ email: "someone@example.com" }));
    expect(sentLink().next).toBe("/");
  });

  it("uses the configured public base URL, not the request Host", async () => {
    await requestLink(makeReq({ email: "someone@example.com" }, { host: "127.0.0.1:15401" }));
    expect(sentLink().url.origin).toBe("https://terminal.test");
  });

  it("ignores forwarded host headers when a public URL is configured", async () => {
    await requestLink(
      makeReq({ email: "someone@example.com" }, { host: "evil.example", "x-forwarded-host": "evil.example", "x-forwarded-proto": "http" }),
    );
    expect(sentLink().url.origin).toBe("https://terminal.test");
  });

  it("uses TERMINAL_URL when NEXTAUTH_URL is unset", async () => {
    vi.stubEnv("NEXTAUTH_URL", "");
    vi.stubEnv("TERMINAL_URL", "https://vexa.example.com/");
    await requestLink(makeReq({ email: "someone@example.com" }));
    expect(sentLink().url.origin).toBe("https://vexa.example.com");
  });
});

describe("the link is built only from a configured public URL", () => {
  it("refuses to send — 503, nothing mailed — when no public URL is configured, whatever the headers say", async () => {
    vi.stubEnv("NEXTAUTH_URL", "");
    vi.stubEnv("TERMINAL_URL", "");
    const res = await requestLink(
      makeReq({ email: "someone@example.com" }, { host: "internal:3000", "x-forwarded-host": "app.dev.vexa.ai", "x-forwarded-proto": "https" }),
    );
    expect(res.status).toBe(503);
    expect(sendMail).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["terminal.test", "javascript:alert(1)", "ftp://terminal.test", "https://user:pw@terminal.test", "https://"])(
    "refuses a configured public URL that is not a plain http(s) origin (%s)",
    async (bad) => {
      vi.stubEnv("NEXTAUTH_URL", bad);
      vi.stubEnv("TERMINAL_URL", "");
      const res = await requestLink(makeReq({ email: "someone@example.com" }));
      expect(res.status).toBe(503);
      expect(sendMail).not.toHaveBeenCalled();
    },
  );

  it("answers the same 503 for every address", async () => {
    vi.stubEnv("NEXTAUTH_URL", "");
    vi.stubEnv("TERMINAL_URL", "");
    const a = await requestLink(makeReq({ email: "known@example.com" }));
    const b = await requestLink(makeReq({ email: "unknown@example.com" }));
    expect([a.status, b.status]).toEqual([503, 503]);
    expect(await a.json()).toEqual(await b.json());
  });
});

describe("no user enumeration", () => {
  it("answers 200 identically whether or not the mail could be delivered", async () => {
    const ok = await requestLink(makeReq({ email: "known@example.com" }));
    expect(ok.status).toBe(200);
    expect(await ok.json()).toEqual({ ok: true });

    sendMail.mockClear();
    sendMail.mockImplementation(async () => {
      throw new Error("connection refused");
    });
    const broken = await requestLink(makeReq({ email: "unknown@example.com" }));
    expect(broken.status).toBe(200);
    expect(await broken.json()).toEqual({ ok: true });
  });
});

describe("what it does refuse", () => {
  it("400s a malformed or missing address without sending anything", async () => {
    for (const body of [{ email: "not-an-email" }, { email: "   " }, {}, { email: 7 }]) {
      const res = await requestLink(makeReq(body));
      expect(res.status).toBe(400);
    }
    expect(sendMail).not.toHaveBeenCalled();
  });

  it("503s — honestly — when the instance cannot sign anything", async () => {
    vi.stubEnv("NEXTAUTH_SECRET", "");
    const res = await requestLink(makeReq({ email: "someone@example.com" }));
    expect(res.status).toBe(503);
    expect(sendMail).not.toHaveBeenCalled();
  });
});
