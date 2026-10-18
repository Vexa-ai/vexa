import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** /api/auth/me — the endpoint the client asks "is this tab still signed in?".
 *
 *  ⚠ THE DEFECT THESE PIN (2026-09-01). It used to answer from cookie PRESENCE alone: no backend
 *  round-trip, so a revoked login token still read as "signed in", the login gate never re-checked,
 *  and the whole shell rendered over an app where every request 401'd. A cookie is not a session.
 *  It now validates against the same `POST /internal/validate` oracle every real route uses.
 *
 *  The fail direction is the other half, and it is deliberate: ONLY a 401 from the oracle signs
 *  anybody out. An unreachable or unconfigured oracle leaves the caller signed in and flagged
 *  `degraded`, because this is a liveness probe, not an authorization boundary — every route that
 *  does something validates independently and fails closed on its own. Ejecting a user because
 *  admin-api blinked would be a worse failure than the one this fixes.
 */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: (name: string, value: string) => { cookieJar[name] = value; },
    delete: (name: string) => { delete cookieJar[name]; },
  }),
}));

import { GET as meRoute } from "../me/route";

/** The oracle, answering however the test says. Records the calls so "did it ask at all?" is
 *  provable rather than inferred. */
function stubOracle(answer: () => Response) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(String(url));
    return answer();
  }));
  return calls;
}

beforeEach(() => {
  cookieJar = {};
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete process.env.VEXA_ADMIN_API_URL;
  delete process.env.VEXA_INTERNAL_API_SECRET;
});

describe("/api/auth/me", () => {
  it("no cookie → 401, and the oracle is not bothered", async () => {
    const calls = stubOracle(() => new Response("{}", { status: 200 }));
    const res = await meRoute();
    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toMatchObject({ authenticated: false, reason: "no_session" });
    expect(calls).toEqual([]);
  });

  it("a REVOKED token → 401, even though the cookie is still sitting there", async () => {
    // The exact state the founder's browser was in: cookie present, token gone.
    cookieJar["vexa-token"] = "revoked-token";
    cookieJar["vexa-user-info"] = JSON.stringify({ email: "founder@vexa.ai", name: "F" });
    const calls = stubOracle(() => new Response(JSON.stringify({ detail: "Invalid token" }), { status: 401 }));

    const res = await meRoute();

    expect(res.status).toBe(401);
    await expect(res.json()).resolves.toMatchObject({ authenticated: false, reason: "session_ended" });
    expect(calls.some((u) => u.includes("/internal/validate"))).toBe(true);
  });

  it("a live token → 200, and the identity comes from the ORACLE, not the cookie", async () => {
    // The user-info cookie is display-only and client-sendable; when the oracle answers, its email
    // is the one that ships.
    cookieJar["vexa-token"] = "good-token";
    cookieJar["vexa-user-info"] = JSON.stringify({ email: "spoofed@evil.example", name: "Real Name" });
    stubOracle(() => new Response(JSON.stringify({ user_id: 58, email: "founder@vexa.ai" }), { status: 200 }));

    const res = await meRoute();

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({
      authenticated: true,
      user: { email: "founder@vexa.ai", name: "Real Name" },
    });
  });

  it("an UNREACHABLE oracle does NOT sign the user out — it degrades", async () => {
    cookieJar["vexa-token"] = "good-token";
    cookieJar["vexa-user-info"] = JSON.stringify({ email: "founder@vexa.ai" });
    stubOracle(() => { throw new Error("ECONNREFUSED"); });

    const res = await meRoute();

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({
      authenticated: true, degraded: true, user: { email: "founder@vexa.ai" },
    });
  });

  it("an UNCONFIGURED oracle degrades too, rather than emptying every session at once", async () => {
    cookieJar["vexa-token"] = "good-token";
    delete process.env.VEXA_INTERNAL_API_SECRET;
    const calls = stubOracle(() => new Response("{}", { status: 200 }));

    const res = await meRoute();

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ authenticated: true, degraded: true });
    expect(calls).toEqual([]); // fail-closed validation never even tries without a secret
  });

  it("a 5xx from the oracle degrades — only 401 means signed out", async () => {
    cookieJar["vexa-token"] = "good-token";
    stubOracle(() => new Response("upstream exploded", { status: 500 }));

    const res = await meRoute();

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toMatchObject({ authenticated: true, degraded: true });
  });

  it("never caches — a session answer read from a cache is a stale session answer", async () => {
    cookieJar["vexa-token"] = "good-token";
    stubOracle(() => new Response(JSON.stringify({ user_id: 1, email: "a@b.c" }), { status: 200 }));
    const res = await meRoute();
    expect(res.headers.get("Cache-Control")).toContain("no-store");
  });
});
