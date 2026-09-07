import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** First-run bootstrap + the company-layer setup gate: the unauthenticated /api/auth/instance probe
 *  and the sign-in claim call. Cookie jar mirrors login.test.ts (the login route sets cookies). */
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
function stubAdminApi(opts: { adminExists: boolean; globalSetup?: "completed" | "missing"; company?: string | null }) {
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
        return new Response(JSON.stringify({
          admin_exists: opts.adminExists,
          global_setup: opts.globalSetup ?? "completed",
          company: opts.company ?? null,
        }), { status: 200 });
      }
      if (url.includes("/internal/signin-allowed")) {
        return new Response(JSON.stringify({ allowed: true, reason: "ok", admin_exists: opts.adminExists, global_setup: opts.globalSetup ?? "completed", company: null }), { status: 200 });
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
    expect(await res.json()).toEqual({ admin_exists: false, global_setup: "completed" });
  });

  it("a configured allowlist counts as an existing admin", async () => {
    // The probe DOES still run, and that is a deliberate change (2026-09-02): an allowlist answers
    // "are there admins", but nothing about an allowlist implies the company layer has been written,
    // and this route now has to report `global_setup` too. What the allowlist still does is WIN on
    // its own question — admin_exists is true here even though the probe says false.
    process.env.VEXA_ADMIN_EMAILS = "dmitry@vexa.ai";
    const calls = stubAdminApi({ adminExists: false, globalSetup: "missing" });
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: true, global_setup: "missing" });
    expect(calls.some((c) => c.url.includes("/internal/instance"))).toBe(true);
  });

  it("probe unreachable → BOTH fields fail safe (plain sign-in, gate down)", async () => {
    // Opposite directions, one failure: admin_exists→true so no dangling claim screen, and
    // global_setup→"completed" so a network blip cannot lock every user out of a working instance.
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: true, global_setup: "completed" });
  });

  it("gate up → global_setup missing is surfaced, and the company name is NOT", async () => {
    // The name is the one field here that is not already visible on the screen; it identifies a
    // customer to any anonymous caller who curls this route, and the sign-in card does not use it.
    stubAdminApi({ adminExists: true, globalSetup: "missing", company: "Acme GmbH" });
    const body = await (await instanceRoute()).json();
    expect(body).toEqual({ admin_exists: true, global_setup: "missing" });
    expect(JSON.stringify(body)).not.toContain("Acme");
  });

  it("an admin-api that has never heard of global_setup reads as completed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ admin_exists: true }), { status: 200 })));
    expect(await (await instanceRoute()).json()).toEqual({ admin_exists: true, global_setup: "completed" });
  });
});

describe("first sign-in claims the admin role", () => {
  // These two drive the claim through the DIRECT login route, which is development-only
  // (production sign-in is the emailed magic link or OAuth). The claim itself is shared
  // machinery — findOrCreateUserToken calls it on every door — so exercising it here still
  // covers the production paths; the route just has to be asked for its dev behaviour.
  beforeEach(() => { vi.stubEnv("NODE_ENV", "development"); });
  afterEach(() => { vi.unstubAllEnvs(); });

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
