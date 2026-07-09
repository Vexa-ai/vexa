import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** First-run bootstrap: the unauthenticated /api/auth/instance probe + the sign-in claim call.
 *  Cookie jar mirrors login.test.ts (the login route sets cookies on success). */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: (name: string, value: string) => { cookieJar[name] = value; },
    delete: (name: string) => { delete cookieJar[name]; },
  }),
}));

import { GET as instanceRoute } from "../instance/route";
import { POST as loginRoute } from "../login/route";

function req(body: unknown) {
  return new Request("http://local/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }) as any;
}

/** admin-api stub: find-or-create + mint + the internal instance/bootstrap edges. */
function stubAdminApi(opts: { adminExists: boolean }) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string });
      if (url.includes("/admin/users/email/")) {
        return new Response(JSON.stringify({ id: 7, email: "new-test@vexa.ai" }), { status: 200 });
      }
      if (url.includes("/tokens")) {
        return new Response(JSON.stringify({ token: "tok-7" }), { status: 201 });
      }
      if (url.includes("/internal/instance")) {
        return new Response(JSON.stringify({ admin_exists: opts.adminExists }), { status: 200 });
      }
      if (url.includes("/internal/bootstrap-admin")) {
        return new Response(JSON.stringify({ claimed: !opts.adminExists, admin_exists: true }), { status: 200 });
      }
      return new Response("nope", { status: 500 });
    }),
  );
  return calls;
}

beforeEach(() => {
  cookieJar = {};
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  delete process.env.VEXA_ADMIN_EMAILS;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete process.env.VEXA_ADMIN_EMAILS;
});

describe("/api/auth/instance — the login surface's claim-screen switch", () => {
  it("no admin anywhere → admin_exists false (claim screen shows)", async () => {
    stubAdminApi({ adminExists: false });
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: false });
  });

  it("a configured allowlist counts as an existing admin — internal probe not even called", async () => {
    process.env.VEXA_ADMIN_EMAILS = "dmitry@vexa.ai";
    const calls = stubAdminApi({ adminExists: false });
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: true });
    expect(calls.length).toBe(0);
  });

  it("probe unreachable → FAIL-SAFE true (plain sign-in, never a dangling claim screen)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: true });
  });
});

describe("first sign-in claims the admin role", () => {
  it("login on a fresh instance POSTs the bootstrap claim with the user's id", async () => {
    const calls = stubAdminApi({ adminExists: false });
    const res = await loginRoute(req({ email: "new-test@vexa.ai" }));
    expect(res.status).toBe(200);
    const claim = calls.find((c) => c.url.includes("/internal/bootstrap-admin"));
    expect(claim).toBeDefined();
    expect(JSON.parse(claim!.body || "{}")).toEqual({ user_id: 7 });
  });

  it("allowlist-run instance → claim machinery stays off", async () => {
    process.env.VEXA_ADMIN_EMAILS = "dmitry@vexa.ai";
    const calls = stubAdminApi({ adminExists: true });
    const res = await loginRoute(req({ email: "new-test@vexa.ai" }));
    expect(res.status).toBe(200);
    expect(calls.some((c) => c.url.includes("/internal/bootstrap-admin"))).toBe(false);
  });
});
