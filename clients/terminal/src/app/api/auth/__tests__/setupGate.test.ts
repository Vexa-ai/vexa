/** The company-layer setup gate at the sign-in doors (founder ruling 2026-09-02: "global needs to
 *  be setup by admin, it just should not let him start the service before that").
 *
 *  Three properties are worth a test each, and each one is a bug that has a name:
 *    1. THE GHOST ACCOUNT — findOrCreateUserToken() creates the user as a side effect, so a refusal
 *       placed after it leaves a real account behind for somebody who was never admitted. Both doors
 *       must ask admin-api BEFORE any user row can exist.
 *    2. THE EATEN LINK — the magic-link route burns the token before it mints a session. A gate
 *       checked after that burn means a colleague's (or a mail scanner's) refused click destroys the
 *       ADMIN's own link, and the admin is locked out of their own setup with no explanation.
 *    3. THE BLIP LOCKOUT — the terminal holds the OPEN half of this gate. An unreachable probe must
 *       fail towards allowed; the flows engine and the operator verbs hold the closed half.
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

import { SETUP_GATE_REFUSAL } from "../adminApi";
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

/** admin-api double whose ONLY interesting behaviour is the gate's verdict. `allowed:null` makes the
 *  gate endpoint fail outright, which is how the fail-safe direction gets tested. */
function stubAdminApi(gate: { allowed: boolean | null }) {
  const calls: string[] = [];
  const spy = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method || "GET"} ${url}`);
    if (url.includes("/internal/signin-allowed")) {
      if (gate.allowed === null) return new Response("upstream exploded", { status: 502 });
      return new Response(JSON.stringify({
        allowed: gate.allowed,
        reason: gate.allowed ? "ok" : "global-setup-in-progress",
        admin_exists: true,
        global_setup: gate.allowed ? "completed" : "missing",
        company: null,
      }), { status: 200 });
    }
    if (url.includes("/admin/users/email/")) {
      return new Response(JSON.stringify({ id: 42, email: "outsider@example.com", name: "Outsider" }), { status: 200 });
    }
    if (url.includes("/tokens")) {
      return init?.method === "POST"
        ? new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 })
        : new Response(JSON.stringify([]), { status: 200 });
    }
    return new Response("nope", { status: 500 });
  });
  vi.stubGlobal("fetch", spy);
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
  vi.stubEnv("VEXA_ADMIN_EMAILS", "");             // no allowlist — the gate is the only authority
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the refusal sentence", () => {
  it("is spelled exactly one way", () => {
    // Every door quotes this constant rather than retyping it; if the wording is ever changed, it is
    // changed here and every screen moves together.
    expect(SETUP_GATE_REFUSAL).toBe("This Vexa is being set up by its administrator.");
  });
});

describe("/api/auth/login while the gate is up", () => {
  it("refuses with the sentence and CREATES NO USER", async () => {
    const calls = stubAdminApi({ allowed: false });
    const res = await login(loginReq({ email: "outsider@example.com" }));

    expect(res.status).toBe(403);
    expect((await res.json()).error).toBe(SETUP_GATE_REFUSAL);
    // The ghost-account property: nothing that could mint an account was called at all.
    expect(calls.some((c) => c.includes("/admin/users"))).toBe(false);
    expect(calls.some((c) => c.includes("/tokens"))).toBe(false);
    expect(setCookies).toHaveLength(0);
  });

  it("lets the admin through", async () => {
    stubAdminApi({ allowed: true });
    const res = await login(loginReq({ email: "outsider@example.com" }));
    expect(res.status).toBe(200);
    expect(setCookies.find((c) => c.name === "vexa-token")?.value).toBe("minted-tok");
  });

  it("an unreachable gate FAILS SAFE — sign-in proceeds", async () => {
    stubAdminApi({ allowed: null });
    const res = await login(loginReq({ email: "outsider@example.com" }));
    expect(res.status).toBe(200);
  });
});

describe("/api/auth/redeem while the gate is up", () => {
  it("answers an HTML card, not JSON, carrying the sentence verbatim", async () => {
    stubAdminApi({ allowed: false });
    const minted = mintMagicToken("outsider@example.com");
    if (!minted.ok) throw new Error("mint failed");

    const res = await redeem(redeemReq({ t: minted.token, next: "/" }));
    expect(res.status).toBe(403);
    // A person who clicked a mail must never be handed a JSON body.
    expect(res.headers.get("content-type")).toContain("text/html");
    expect(await res.text()).toContain(SETUP_GATE_REFUSAL);
    expect(res.cookies.get("vexa-token")).toBeUndefined();
  });

  it("DOES NOT BURN THE LINK — the same link still works once the gate lifts", async () => {
    // The whole reason the guard sits on the pure verifier instead of after redeemMagicToken(): a
    // refused click (a forwarded mail, a link-following mail scanner) must not consume the one
    // credential that unlocks the instance.
    stubAdminApi({ allowed: false });
    const minted = mintMagicToken("outsider@example.com");
    if (!minted.ok) throw new Error("mint failed");
    expect((await redeem(redeemReq({ t: minted.token, next: "/" }))).status).toBe(403);

    stubAdminApi({ allowed: true });
    const second = await redeem(redeemReq({ t: minted.token, next: "/" }));
    expect(second.status).toBe(302);
    expect(second.cookies.get("vexa-token")?.value).toBe("minted-tok");
  });

  it("creates no user for a refused click", async () => {
    const calls = stubAdminApi({ allowed: false });
    const minted = mintMagicToken("outsider@example.com");
    if (!minted.ok) throw new Error("mint failed");
    await redeem(redeemReq({ t: minted.token, next: "/" }));
    expect(calls.some((c) => c.includes("/admin/users"))).toBe(false);
    expect(calls.some((c) => c.includes("/tokens"))).toBe(false);
  });

  it("still refuses a REPLAY before it ever asks the gate", async () => {
    // The pre-flight must not resurrect a burned link: the burn still happens in redeemMagicToken(),
    // and a second click gets the ordinary "already used" card.
    stubAdminApi({ allowed: true });
    const minted = mintMagicToken("outsider@example.com");
    if (!minted.ok) throw new Error("mint failed");
    expect((await redeem(redeemReq({ t: minted.token, next: "/" }))).status).toBe(302);
    const second = await redeem(redeemReq({ t: minted.token, next: "/" }));
    expect(second.status).toBe(410);
    expect(await second.text()).toContain("already used");
  });

  it("a forged link never reaches the gate probe at all", async () => {
    const calls = stubAdminApi({ allowed: false });
    const res = await redeem(redeemReq({ t: "garbage", next: "/" }));
    expect(res.status).toBe(400);
    expect(calls.some((c) => c.includes("/internal/signin-allowed"))).toBe(false);
  });
});
