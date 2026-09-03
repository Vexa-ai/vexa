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
  // The route is DEVELOPMENT-ONLY (see the production test at the bottom); vitest runs with
  // NODE_ENV=test, so the dev behaviour has to be asked for explicitly.
  vi.stubEnv("NODE_ENV", "development");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("/api/auth/login — direct email login against a mocked admin-api", () => {
  it("finds an existing user, mints a token, and sets both cookies", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method || "GET"} ${url}`);
        if (url.includes("/admin/users/email/")) {
          return new Response(JSON.stringify({ id: 42, email: "test-a@b.com", name: "A" }), { status: 200 });
        }
        if (url.includes("/tokens")) {
          return new Response(JSON.stringify({ token: "minted-tok" }), { status: 200 });
        }
        return new Response("nope", { status: 500 });
      }),
    );

    const res = await login(makeReq({ email: "test-a@b.com" }));
    expect(res.status).toBe(200);

    // No create call — user already existed.
    expect(calls.some((c) => c.includes("/admin/users/email/"))).toBe(true);
    expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/admin/users"))).toBe(false);
    expect(calls.some((c) => c.includes("/tokens"))).toBe(true);
    // an EXISTING user is not re-provisioned (eager provisioning fires only on account creation)
    expect(calls.some((c) => c.includes("/agent/workspace/init"))).toBe(false);

    const tok = setCookies.find((c) => c.name === "vexa-token");
    const info = setCookies.find((c) => c.name === "vexa-user-info");
    expect(tok?.value).toBe("minted-tok");
    // `id` is part of the info cookie and is LOAD-BEARING: the minutes seams
    // (api/minutes/ensure-meeting, api/minutes/person-state) read the user id straight out of it.
    expect(JSON.parse(info!.value)).toEqual({ id: 42, email: "test-a@b.com", name: "A" });
    expect((tok?.opts as { httpOnly?: boolean })?.httpOnly).toBe(true);
  });

  it("accepts ANY address in development — the old `includes(\"test\")` filter is gone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/admin/users/email/")) {
          return new Response(JSON.stringify({ id: 9, email: "real@company.com" }), { status: 200 });
        }
        if (url.includes("/tokens")) return new Response(JSON.stringify({ token: "tok-9" }), { status: 200 });
        return new Response("nope", { status: 500 });
      }),
    );
    const res = await login(makeReq({ email: "real@company.com" }));
    expect(res.status).toBe(200);
  });

  it("creates the user when admin-api returns 404, then mints a token", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push(`${init?.method || "GET"} ${url}`);
        if (url.includes("/admin/users/email/")) return new Response("not found", { status: 404 });
        if (init?.method === "POST" && url.endsWith("/admin/users")) {
          return new Response(JSON.stringify({ id: 7, email: "test-new@b.com" }), { status: 201 });
        }
        if (url.includes("/tokens")) return new Response(JSON.stringify({ token: "tok-7" }), { status: 200 });
        return new Response("nope", { status: 500 });
      }),
    );

    const res = await login(makeReq({ email: "test-new@b.com" }));
    expect(res.status).toBe(200);
    expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/admin/users"))).toBe(true);
    expect(setCookies.find((c) => c.name === "vexa-token")?.value).toBe("tok-7");
    // a NEW account eagerly provisions the agent workspace over the gateway (best-effort — a 500 here
    // is swallowed, so sign-in still succeeds above); it authenticates with the freshly minted token
    const provision = calls.find((c) => c.includes("/agent/workspace/init"));
    expect(provision).toBeTruthy();
    expect(provision!.startsWith("POST")).toBe(true);
  });

  it("rejects a malformed email without calling admin-api", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await login(makeReq({ email: "not-an-email" }));
    expect(res.status).toBe(400);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("/api/auth/login — DEAD in production", () => {
  /** The bypass this route used to be: any address containing "test" got a real session on a
   *  production deploy, and in minutes mode ANY address did. Production sign-in is now the emailed
   *  magic link (request-link → redeem) or OAuth; this route may not mint a session outside
   *  development, and must refuse before it reads the body or touches admin-api. */
  for (const env of ["production", "test"]) {
    it(`refuses every address under NODE_ENV=${env}, body unread`, async () => {
      vi.stubEnv("NODE_ENV", env);
      vi.stubEnv("NEXT_PUBLIC_TERMINAL_MODE", "minutes"); // the old minutes-mode "any address" hole
      const fetchSpy = vi.fn();
      vi.stubGlobal("fetch", fetchSpy);

      for (const email of ["test-a@b.com", "anyone@anywhere.com"]) {
        const res = await login(makeReq({ email }));
        expect(res.status).toBe(403);
      }
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(setCookies).toHaveLength(0);
    });
  }
});
