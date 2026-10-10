/** /api/auth/redeem — the BACK half of the magic-link door, exercised as a route.
 *
 *  magicToken.test.ts owns the crypto; this file owns what the ROUTE does with it: a good link
 *  sets the same two httpOnly cookies the direct-login route sets and 302s to the deeplink; a
 *  replayed, expired, or forged link mints nothing; and a hostile `next=` cannot bounce the
 *  recipient off this origin.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET as redeem } from "../redeem/route";
import { mintMagicToken } from "../magicToken";
import { linkLedger } from "./linkLedgerDouble";

function makeReq(query: Record<string, string>): import("next/server").NextRequest {
  const url = new URL("https://terminal.test/api/auth/redeem");
  for (const [k, v] of Object.entries(query)) url.searchParams.set(k, v);
  return { nextUrl: url, url: url.toString() } as unknown as import("next/server").NextRequest;
}

/** admin-api double: find-or-create returns a user, the token mint returns a value. Everything
 *  else (prune list, bootstrap-admin, workspace provisioning) is best-effort in adminApi.ts and
 *  is allowed to fail. */
function stubAdminApi() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    // admin-api's single-use record for links: the first redeem of a jti is the only one admitted.
    if (linkLedger.isRedeem(url)) return linkLedger.respond(init);
    // Vexa-ai/vexa#1783: the redeem asks whether this address may sign in before it creates
    // anything. These cases are about the link; the refusals are in signinAllowList.test.ts.
    if (url.includes("/internal/signin-admission")) {
      return new Response(JSON.stringify({ admitted: true, why: "existing-user" }), { status: 200 });
    }
    if (url.includes("/admin/users/email/")) {
      return new Response(JSON.stringify({ id: 42, email: "magic@example.com", name: "Magic" }), { status: 200 });
    }
    // POST /tokens mints; GET /tokens is the login-token prune's listing (an array).
    if (url.includes("/tokens")) {
      return init?.method === "POST"
        ? new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 })
        : new Response(JSON.stringify([]), { status: 200 });
    }
    return new Response("nope", { status: 500 });
  });
}

beforeEach(() => {
  linkLedger.reset();
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret-0123456789abcdef");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-secret");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("a valid link", () => {
  it("sets both httpOnly cookies and 302s to the deeplink it carried", async () => {
    vi.stubGlobal("fetch", stubAdminApi());
    const minted = mintMagicToken("magic@example.com");
    expect(minted.ok).toBe(true);
    if (!minted.ok) return;

    const res = await redeem(makeReq({ t: minted.token, next: "/?ask=catch-up" }));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/?ask=catch-up");

    const tok = res.cookies.get("vexa-token");
    const info = res.cookies.get("vexa-user-info");
    expect(tok?.value).toBe("minted-tok");
    expect(tok?.httpOnly).toBe(true);
    expect(tok?.secure).toBe(true); // TERMINAL_URL is https
    // `id` is load-bearing downstream (the minutes seams read it out of the info cookie).
    expect(JSON.parse(info!.value)).toEqual({ id: 42, email: "magic@example.com", name: "Magic" });
  });

  it("defaults to / when no next is given", async () => {
    vi.stubGlobal("fetch", stubAdminApi());
    const minted = mintMagicToken("magic@example.com");
    if (!minted.ok) throw new Error("mint failed");
    const res = await redeem(makeReq({ t: minted.token }));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/");
  });
});

describe("open-redirect guard", () => {
  it("refuses to send the recipient off-origin, but still signs them in", async () => {
    for (const hostile of ["https://evil.example/steal", "//evil.example", "/\\evil.example"]) {
      linkLedger.reset();
      vi.stubGlobal("fetch", stubAdminApi());
      const minted = mintMagicToken("magic@example.com");
      if (!minted.ok) throw new Error("mint failed");
      const res = await redeem(makeReq({ t: minted.token, next: hostile }));
      expect(res.status).toBe(302);
      expect(res.headers.get("location")).toBe("/");
    }
  });
});

describe("a link that must not work", () => {
  it("is refused on REPLAY, and mints nothing the second time", async () => {
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    const minted = mintMagicToken("magic@example.com");
    if (!minted.ok) throw new Error("mint failed");

    expect((await redeem(makeReq({ t: minted.token, next: "/" }))).status).toBe(302);
    const callsAfterFirst = fetchSpy.mock.calls.length;

    const second = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(second.status).toBe(410);
    expect(second.headers.get("content-type")).toContain("text/html");
    expect(await second.text()).toContain("already used");
    expect(second.cookies.get("vexa-token")).toBeUndefined();
    // one round-trip, to the shared single-use record, and nothing that could create or mint
    const later = fetchSpy.mock.calls.slice(callsAfterFirst).map((c) => String(c[0]));
    expect(later).toHaveLength(1);
    expect(linkLedger.isRedeem(later[0])).toBe(true);
  });

  it("is refused when ANOTHER terminal replica already redeemed it", async () => {
    // The record is admin-api's, shared by every replica: a jti some other process redeemed is used
    // here too, although this process has never seen it.
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    const minted = mintMagicToken("magic@example.com");
    if (!minted.ok) throw new Error("mint failed");
    await linkLedger.redeem(minted.jti, minted.expiresAt);       // the other replica's redeem

    const res = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(res.status).toBe(410);
    expect(await res.text()).toContain("already used");
    expect(res.cookies.get("vexa-token")).toBeUndefined();
    expect(fetchSpy.mock.calls.map((c) => String(c[0])).some((u) => u.includes("/tokens"))).toBe(false);
  });

  it("is refused while the shared record cannot be checked, and stays usable afterwards", async () => {
    vi.stubGlobal("fetch", stubAdminApi());
    const minted = mintMagicToken("magic@example.com");
    if (!minted.ok) throw new Error("mint failed");

    linkLedger.down = true;
    const refused = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(refused.status).toBe(503);
    expect(refused.cookies.get("vexa-token")).toBeUndefined();

    linkLedger.down = false;
    const later = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(later.status).toBe(302);
  });

  it("sends the link's jti and expiry to admin-api, over the internal edge", async () => {
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    const minted = mintMagicToken("magic@example.com");
    if (!minted.ok) throw new Error("mint failed");
    await redeem(makeReq({ t: minted.token, next: "/" }));
    const call = fetchSpy.mock.calls.find((c) => linkLedger.isRedeem(String(c[0])));
    expect(call).toBeTruthy();
    const init = call![1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ jti: minted.jti, expires_at: minted.expiresAt });
    expect((init.headers as Record<string, string>)["X-Internal-Secret"]).toBe("internal-secret");
  });

  it("is refused when EXPIRED", async () => {
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    const minted = mintMagicToken("magic@example.com", { now: Date.now() - 3_600_000, ttl: 900 });
    if (!minted.ok) throw new Error("mint failed");
    const res = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(res.status).toBe(410);
    expect(await res.text()).toContain("expired");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("is refused when FORGED or absent", async () => {
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    for (const junk of ["", "garbage", "aaa.bbb"]) {
      const res = await redeem(makeReq({ t: junk, next: "/" }));
      expect(res.status).toBe(400);
      expect(await res.text()).toContain("not valid");
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("is refused when the instance has no signing secret", async () => {
    const minted = mintMagicToken("magic@example.com");
    if (!minted.ok) throw new Error("mint failed");
    linkLedger.reset();
    vi.stubEnv("NEXTAUTH_SECRET", "");
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(res.status).toBe(503);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
