import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Cookie jar the mocked next/headers writes into, so the test can assert what login set. */
let setCookies: Array<{ name: string; value: string; opts?: unknown }> = [];

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: () => undefined,
    set: (name: string, value: string, opts?: unknown) => setCookies.push({ name, value, opts }),
    delete: () => {},
  }),
}));

import { POST as login } from "../login/route";

function makeReq(body: unknown): import("next/server").NextRequest {
  return { json: async () => body } as unknown as import("next/server").NextRequest;
}

beforeEach(() => {
  setCookies = [];
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-secret";
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("/api/auth/login — direct email login against a mocked admin-api", () => {
  it("finds an existing user, mints a token, and sets both cookies", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method || "GET"} ${url}`);
        if (url.includes("/admin/users/email/")) {
          return new Response(JSON.stringify({ id: 42, email: "a@b.com", name: "A" }), { status: 200 });
        }
        if (url.includes("/tokens")) {
          return new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 });
        }
        return new Response("nope", { status: 500 });
      }),
    );

    const res = await login(makeReq({ email: "a@b.com" }));
    expect(res.status).toBe(200);

    // No create call — user already existed.
    expect(calls.some((c) => c.includes("/admin/users/email/"))).toBe(true);
    expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/admin/users"))).toBe(false);
    expect(calls.some((c) => c.includes("/tokens"))).toBe(true);

    const tok = setCookies.find((c) => c.name === "vexa-token");
    const info = setCookies.find((c) => c.name === "vexa-user-info");
    expect(tok?.value).toBe("minted-tok");
    expect(JSON.parse(info!.value)).toEqual({ email: "a@b.com" });
    expect((tok?.opts as { httpOnly?: boolean })?.httpOnly).toBe(true);
  });

  it("creates the user when admin-api returns 404, then mints a token", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method || "GET"} ${url}`);
        if (url.includes("/admin/users/email/")) return new Response("not found", { status: 404 });
        if (init?.method === "POST" && url.endsWith("/admin/users")) {
          return new Response(JSON.stringify({ id: 7, email: "new@b.com" }), { status: 201 });
        }
        if (url.includes("/tokens")) return new Response(JSON.stringify({ token: "tok-7" }), { status: 200 });
        return new Response("nope", { status: 500 });
      }),
    );

    const res = await login(makeReq({ email: "new@b.com" }));
    expect(res.status).toBe(200);
    expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/admin/users"))).toBe(true);
    expect(setCookies.find((c) => c.name === "vexa-token")?.value).toBe("tok-7");
  });

  it("rejects a malformed email without calling admin-api", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await login(makeReq({ email: "not-an-email" }));
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
