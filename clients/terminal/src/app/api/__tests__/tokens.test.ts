import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Cookie jar the mocked next/headers reads from — set per test to simulate the logged-in user. */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: () => {},
    delete: () => {},
  }),
}));

import { GET as listRoute, POST as createRoute } from "../tokens/route";
import { DELETE as deleteRoute } from "../tokens/[id]/route";

function makeReq(body: unknown): import("next/server").NextRequest {
  return { json: async () => body } as unknown as import("next/server").NextRequest;
}

const login = (email = "alice@vexa.ai") => {
  cookieJar = { "vexa-token": "cookie-tok", "vexa-user-info": JSON.stringify({ email, name: "Alice" }) };
};

/** A fake admin-api: user 42 (alice) owns tokens 1 and 2; every call is recorded for scoping asserts. */
function stubAdminApi() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method || "GET"} ${url}`);
      if (url.includes("/admin/users/email/")) {
        return new Response(JSON.stringify({ id: 42, email: "alice@vexa.ai" }), { status: 200 });
      }
      if (url.includes("/admin/users/42/tokens") && (init?.method || "GET") === "GET") {
        return new Response(JSON.stringify([
          { id: 1, user_id: 42, scopes: ["bot"], name: "ci" },
          { id: 2, user_id: 42, scopes: ["bot", "tx"], name: null },
        ]), { status: 200 });
      }
      if (url.includes("/admin/users/42/tokens") && init?.method === "POST") {
        return new Response(JSON.stringify({ id: 3, user_id: 42, scopes: ["bot"], name: "new", token: "vxa_bot_secret" }), { status: 201 });
      }
      if (url.includes("/admin/tokens/") && init?.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return new Response("nope", { status: 500 });
    }),
  );
  return calls;
}

beforeEach(() => {
  cookieJar = {};
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-secret";
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("/api/tokens — self-serve, always scoped to the cookie-resolved user", () => {
  it("GET lists the logged-in user's tokens (user_id resolved from the cookie email, never the client)", async () => {
    login();
    const calls = stubAdminApi();
    const res = await listRoute();
    expect(res.status).toBe(200);
    const { tokens } = await res.json();
    expect(tokens.map((t: { id: number }) => t.id)).toEqual([1, 2]);
    expect(calls.some((c) => c.includes("/admin/users/email/alice%40vexa.ai"))).toBe(true);
    expect(calls.some((c) => c.startsWith("GET") && c.includes("/admin/users/42/tokens"))).toBe(true);
  });

  it("GET without auth cookies → 401, admin-api never called", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await listRoute();
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("POST mints for the resolved user and returns the token value once", async () => {
    login();
    const calls = stubAdminApi();
    const res = await createRoute(makeReq({ scopes: ["bot"], name: "new", expiresIn: 3600 }));
    expect(res.status).toBe(201);
    const { token } = await res.json();
    expect(token.token).toBe("vxa_bot_secret");
    const mint = calls.find((c) => c.startsWith("POST"));
    expect(mint).toContain("/admin/users/42/tokens");   // the cookie-resolved user
    expect(mint).toContain("scopes=bot");
    expect(mint).toContain("name=new");
    expect(mint).toContain("expires_in=3600");
  });

  it("POST rejects an invalid or empty scope set without calling admin-api's mint", async () => {
    login();
    const calls = stubAdminApi();
    expect((await createRoute(makeReq({ scopes: ["root"] }))).status).toBe(400);
    expect((await createRoute(makeReq({ scopes: [] }))).status).toBe(400);
    expect(calls.some((c) => c.startsWith("POST"))).toBe(false);
  });

  it("DELETE revokes an owned token", async () => {
    login();
    const calls = stubAdminApi();
    const res = await deleteRoute(new Request("http://t/api/tokens/2"), { params: Promise.resolve({ id: "2" }) });
    expect(res.status).toBe(200);
    expect(calls.some((c) => c === "DELETE http://admin.test/admin/tokens/2")).toBe(true);
  });

  it("DELETE refuses a token the user doesn't own (404), never forwarding the revoke", async () => {
    login();
    const calls = stubAdminApi();
    const res = await deleteRoute(new Request("http://t/api/tokens/99"), { params: Promise.resolve({ id: "99" }) });
    expect(res.status).toBe(404);
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
  });
});
