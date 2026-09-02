/** POST /api/auth/claim-admin — the claim reachable by a session that already exists.
 *
 *  ⚠ WHY THE ROUTE EXISTS (observed live 2026-09-02, 08:48Z). The admin role could only be claimed
 *  inside findOrCreateUserToken, i.e. only while walking through a sign-in door. An instance whose
 *  sessions predate the gate therefore had no reachable claim at all — the founder's cookie was
 *  valid, admin_exists was false, and a cookie never traverses a sign-in door twice. The instance
 *  said "not set up" forever with nothing able to set it up.
 *
 *  Three properties, in descending order of what they cost when wrong:
 *    1. IDENTITY comes from the validated `vexa-token` ONLY. `vexa-user-info` is client-forgeable
 *       (httpOnly stops a script, not a curl), and this route grants the highest privilege the
 *       product has.
 *    2. It FAILS CLOSED — the opposite direction from the rest of the gate — because guessing wrong
 *       here grants admin rather than merely opening a door.
 *    3. It never TRANSFERS the role: an instance that already has an admin is refused.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: (name: string, value: string) => { cookieJar[name] = value; },
    delete: (name: string) => { delete cookieJar[name]; },
  }),
}));

import { POST as claimAdmin } from "../claim-admin/route";

/** admin-api double: the validate oracle, the instance probe, and the bootstrap-admin write. */
function stubAdminApi(opts: {
  validate?: { status: number; body?: unknown };
  adminExists?: boolean;
  instanceFails?: boolean;
  bootstrap?: { status: number; body?: unknown };
}) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push(`${init?.method || "GET"} ${u}`);
    if (u.includes("/internal/validate")) {
      const v = opts.validate ?? { status: 200, body: { user_id: 11, email: "dmitry@vexa.ai", is_admin: false } };
      return new Response(JSON.stringify(v.body ?? {}), { status: v.status });
    }
    if (u.includes("/internal/instance")) {
      if (opts.instanceFails) throw new Error("ECONNREFUSED");
      return new Response(JSON.stringify({ admin_exists: opts.adminExists ?? false, global_setup: "missing" }), { status: 200 });
    }
    if (u.includes("/internal/bootstrap-admin")) {
      const b = opts.bootstrap ?? { status: 200, body: { claimed: true } };
      return new Response(JSON.stringify(b.body ?? {}), { status: b.status });
    }
    return new Response("nope", { status: 500 });
  }));
  return calls;
}

beforeEach(() => {
  cookieJar = { "vexa-token": "a-real-session-token" };
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-key");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("VEXA_ADMIN_EMAILS", "");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the happy path", () => {
  it("claims with the id the ORACLE returned, never one from a cookie", async () => {
    // The info cookie says user 999; the oracle says 11. Only 11 may reach bootstrap-admin.
    cookieJar["vexa-user-info"] = JSON.stringify({ id: 999, email: "attacker@example.com" });
    const calls = stubAdminApi({});
    const spy = vi.mocked(globalThis.fetch);

    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ success: true, claimed: true, email: "dmitry@vexa.ai" });

    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(true);
    const write = spy.mock.calls.find(([u]) => String(u).includes("/internal/bootstrap-admin"));
    expect(JSON.parse(String((write![1] as RequestInit).body))).toEqual({ user_id: 11 });
  });
});

describe("who may claim", () => {
  it("no cookie → 401, and nothing is asked of admin-api", async () => {
    cookieJar = {};
    const calls = stubAdminApi({});
    const res = await claimAdmin();
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("a token the oracle REFUSES → 401, no claim written", async () => {
    const calls = stubAdminApi({ validate: { status: 401 } });
    const res = await claimAdmin();
    expect(res.status).toBe(401);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("an unreachable oracle FAILS CLOSED — 503, no claim written", async () => {
    // The opposite direction from instanceState() on purpose: a wrong guess here grants admin.
    const calls = stubAdminApi({ validate: { status: 502 } });
    const res = await claimAdmin();
    expect(res.status).toBe(503);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("an unreachable instance probe also fails closed rather than granting", async () => {
    // instanceState() fails safe towards admin_exists:true, which lands here as "already claimed" —
    // i.e. the fail-safe that opens sign-in is the one that REFUSES a claim. Both directions are the
    // cautious one for their own consequence.
    const calls = stubAdminApi({ instanceFails: true });
    const res = await claimAdmin();
    expect(res.status).toBe(409);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });
});

describe("it can never transfer the role", () => {
  it("an instance that already has an admin is refused, and told to reload", async () => {
    const calls = stubAdminApi({ adminExists: true });
    const res = await claimAdmin();
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({
      error: "This instance already has an administrator.", admin_exists: true, reload: true,
    });
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("losing admin-api's race is reported honestly, not as a claim", async () => {
    // admin-api serialises concurrent claims; the loser gets claimed:false. An admin exists either
    // way and the page reloads onto the right screen — but this session did not claim it.
    stubAdminApi({ bootstrap: { status: 200, body: { claimed: false } } });
    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect((await res.json()).claimed).toBe(false);
  });

  it("a bootstrap write that fails is surfaced, not swallowed", async () => {
    stubAdminApi({ bootstrap: { status: 500, body: { detail: "boom" } } });
    const res = await claimAdmin();
    expect(res.status).toBe(500);
    expect((await res.json()).error).toContain("Could not claim this instance");
  });
});
