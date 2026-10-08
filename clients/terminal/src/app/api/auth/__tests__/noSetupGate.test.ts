/** The sign-in doors after the company-layer gate was removed (founder ruling 2026-10-08: "let's
 *  remove global setup at all so that there is no need to setup global at all - let it be empty
 *  with no data - it's fine").
 *
 *  Until then every door asked admin-api `/internal/signin-allowed` before it created anything, and
 *  refused everybody but the administrator while `_global` was unwritten. Now no door asks: an
 *  admin-api that would still answer "refused" is never consulted, and an ordinary person gets a
 *  session on an instance whose `_global` is empty.
 *
 *  (Doors DO ask a different question now — `/internal/signin-admission`, whether this address may
 *  sign in at all (Vexa-ai/vexa#1783). It is about the person, never about `_global`; the member here
 *  has an account, so it admits them. See signinAllowList.test.ts.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let setCookies: Array<{ name: string; value: string }> = [];
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => undefined,
    set: (name: string, value: string) => setCookies.push({ name, value }),
    delete: () => {},
  }),
}));

import { POST as login } from "../login/route";
import { GET as redeem } from "../redeem/route";
import { _resetJtiLedger, mintMagicToken } from "../magicToken";

function loginReq(body: unknown): import("next/server").NextRequest {
  return { json: async () => body } as unknown as import("next/server").NextRequest;
}

function redeemReq(query: Record<string, string>): import("next/server").NextRequest {
  const url = new URL("https://terminal.test/api/auth/redeem");
  for (const [k, v] of Object.entries(query)) url.searchParams.set(k, v);
  return { nextUrl: url, url: url.toString() } as unknown as import("next/server").NextRequest;
}

/** An admin-api from before the change: it would still refuse this address if asked. */
function stubAdminApi() {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method || "GET"} ${url}`);
    // The allow-list's admission door (Vexa-ai/vexa#1783) is a DIFFERENT question from the removed
    // company-layer gate: this member already has an account, so it admits them.
    if (url.includes("/internal/signin-admission")) {
      return new Response(JSON.stringify({ admitted: true, why: "existing-user" }), { status: 200 });
    }
    if (url.includes("/internal/signin-allowed")) {
      return new Response(JSON.stringify({ allowed: false, reason: "This Vexa is being set up by its administrator." }), { status: 200 });
    }
    if (url.includes("/internal/instance")) {
      return new Response(JSON.stringify({ admin_exists: true, global_setup: "missing" }), { status: 200 });
    }
    if (url.includes("/admin/users/email/")) {
      return new Response(JSON.stringify({ id: 42, email: "member@example.com", name: "Member" }), { status: 200 });
    }
    if (url.includes("/tokens")) {
      return init?.method === "POST"
        ? new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 })
        : new Response(JSON.stringify([]), { status: 200 });
    }
    if (url.includes("/internal/bootstrap-admin")) {
      return new Response(JSON.stringify({ claimed: false, admin_exists: true }), { status: 200 });
    }
    return new Response("nope", { status: 500 });
  }));
  return calls;
}

beforeEach(() => {
  setCookies = [];
  _resetJtiLedger();
  vi.stubEnv("NODE_ENV", "development");           // the direct login route is dev-only
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-secret");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("VEXA_ADMIN_EMAILS", "");
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "info").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("no door is gated on `_global`", () => {
  it("/api/auth/login signs a non-admin in, and never asks signin-allowed", async () => {
    const calls = stubAdminApi();
    const res = await login(loginReq({ email: "member@example.com" }));
    expect(res.status).toBe(200);
    expect(setCookies.find((c) => c.name === "vexa-token")?.value).toBe("minted-tok");
    expect(calls.some((c) => c.includes("/internal/signin-allowed"))).toBe(false);
  });

  it("/api/auth/redeem signs a non-admin in, and never asks signin-allowed", async () => {
    const calls = stubAdminApi();
    const minted = mintMagicToken("member@example.com");
    if (!minted.ok) throw new Error("mint failed");
    const res = await redeem(redeemReq({ t: minted.token, next: "/?ask=catch-up" }));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/?ask=catch-up");
    expect(res.cookies.get("vexa-token")?.value).toBe("minted-tok");
    expect(calls.some((c) => c.includes("/internal/signin-allowed"))).toBe(false);
  });

  it("a replayed link is still refused — the link stays single-use", async () => {
    stubAdminApi();
    const minted = mintMagicToken("member@example.com");
    if (!minted.ok) throw new Error("mint failed");
    expect((await redeem(redeemReq({ t: minted.token, next: "/?ask=catch-up" }))).status).toBe(302);
    const second = await redeem(redeemReq({ t: minted.token, next: "/?ask=catch-up" }));
    expect(second.status).toBe(410);
    expect(await second.text()).toContain("already used");
  });
});
