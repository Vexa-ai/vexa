/** /api/auth/redeem — the BACK half of the magic-link door, exercised as a route.
 *
 *  magicToken.test.ts owns the crypto; this file owns what the ROUTE does with it: a good link
 *  sets the same two httpOnly cookies the direct-login route sets and 302s to the deeplink; a
 *  replayed, expired, or forged link mints nothing; and a hostile `next=` cannot bounce the
 *  recipient off this origin.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET as redeem } from "../redeem/route";
import { _resetJtiLedger, mintMagicToken } from "../magicToken";

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
  _resetJtiLedger();
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-secret");
  vi.stubEnv("VEXA_ADMIN_EMAILS", "admin@example.com"); // allowlist → bootstrap claim stays off
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
      _resetJtiLedger();
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
    expect(fetchSpy.mock.calls.length).toBe(callsAfterFirst); // no admin-api round-trip at all
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
    _resetJtiLedger();
    vi.stubEnv("NEXTAUTH_SECRET", "");
    const fetchSpy = stubAdminApi();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await redeem(makeReq({ t: minted.token, next: "/" }));
    expect(res.status).toBe(503);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
