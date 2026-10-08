import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** First-run bootstrap: the unauthenticated /api/auth/instance probe and the sign-in claim call.
 *  Cookie jar mirrors login.test.ts (the login route sets cookies). */
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
function stubAdminApi(opts: { adminExists: boolean; company?: string | null }) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string });
      // Vexa-ai/vexa#1783: admission comes first. On an instance nobody has claimed, the sign-in
      // that carries the admin claim code is admitted — that sign-in IS the claim.
      if (url.includes("/internal/signin-admission")) {
        return new Response(JSON.stringify({
          admitted: true, why: opts.adminExists ? "existing-user" : "claim-code",
        }), { status: 200 });
      }
      if (url.includes("/admin/users/email/")) {
        return new Response(JSON.stringify({ id: 7, email: "new-test@vexa.ai" }), { status: 200 });
      }
      if (url.includes("/tokens")) {
        return new Response(JSON.stringify({ token: "tok-7" }), { status: 201 });
      }
      if (url.includes("/internal/instance")) {
        // An admin-api from before 2026-10-08 still sends `global_setup` and `company`; neither may
        // cross this route.
        return new Response(JSON.stringify({
          admin_exists: opts.adminExists,
          global_setup: "missing",
          company: opts.company ?? null,
        }), { status: 200 });
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

  it("is admin-api's answer alone — an admin list in the terminal's environment is not consulted", async () => {
    // admin-api counts its own VEXA_ADMIN_EMAILS as admins; the terminal has no list to add.
    process.env.VEXA_ADMIN_EMAILS = "dmitry@vexa.ai";
    stubAdminApi({ adminExists: false });
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: false });
  });

  it("probe unreachable → fails safe to plain sign-in", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await instanceRoute();
    expect(await res.json()).toEqual({ admin_exists: true });
  });

  it("carries no company-layer state and no company name (founder ruling 2026-10-08)", async () => {
    stubAdminApi({ adminExists: true, company: "Acme GmbH" });
    const body = await (await instanceRoute()).json();
    expect(body).toEqual({ admin_exists: true });
    expect(JSON.stringify(body)).not.toContain("Acme");
  });
});

describe("first sign-in claims the admin role", () => {
  // These two drive the claim through the DIRECT login route, which is development-only
  // (production sign-in is the emailed magic link or OAuth). The claim itself is shared
  // machinery — findOrCreateUserToken calls it on every door — so exercising it here still
  // covers the production paths; the route just has to be asked for its dev behaviour.
  beforeEach(() => { vi.stubEnv("NODE_ENV", "development"); });
  afterEach(() => { vi.unstubAllEnvs(); });

  it("login on a fresh instance POSTs the bootstrap claim with the user's id and the claim code", async () => {
    cookieJar["vexa-claim-code"] = "ABCD-EF01-JKMN-PQRS"; // entered on the claim screen
    const calls = stubAdminApi({ adminExists: false });
    const res = await loginRoute(req({ email: "new-test@vexa.ai" }));
    expect(res.status).toBe(200);
    const admission = calls.find((c) => c.url.includes("/internal/signin-admission"));
    expect(JSON.parse(admission!.body || "{}").claim_code).toBe("ABCD-EF01-JKMN-PQRS");
    const claim = calls.find((c) => c.url.includes("/internal/bootstrap-admin"));
    expect(claim).toBeDefined();
    expect(JSON.parse(claim!.body || "{}")).toEqual({ user_id: 7, claim_code: "ABCD-EF01-JKMN-PQRS" });
  });

  it("no code, no claim: a sign-in that carries none never asks for the role", async () => {
    const calls = stubAdminApi({ adminExists: true });
    const res = await loginRoute(req({ email: "new-test@vexa.ai" }));
    expect(res.status).toBe(200);
    expect(calls.some((c) => c.url.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("whether the claim lands is admin-api's answer, whatever the terminal's environment says", async () => {
    process.env.VEXA_ADMIN_EMAILS = "dmitry@vexa.ai";
    cookieJar["vexa-claim-code"] = "ABCD-EF01-JKMN-PQRS";
    const calls = stubAdminApi({ adminExists: true });
    const res = await loginRoute(req({ email: "new-test@vexa.ai" }));
    expect(res.status).toBe(200);
    // asked, and told no: the decision is upstream
    expect(calls.some((c) => c.url.includes("/internal/bootstrap-admin"))).toBe(true);
  });
});
