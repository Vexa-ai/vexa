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

import { requireAdmin } from "../admin/gate";
import { GET as meRoute } from "../admin/me/route";
import { GET as overviewRoute } from "../admin/overview/route";
import { POST as probeRoute } from "../admin/probe/route";

/** A fake admin-api /internal/validate: token "admin-tok" → dmitry (allowlisted), "user-tok" → bob. */
function stubValidate() {
  const calls: { url: string; secret?: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({
        url,
        secret: (init?.headers as Record<string, string>)?.["X-Internal-Secret"],
        body: init?.body as string,
      });
      if (url.includes("/internal/validate")) {
        const { token } = JSON.parse((init?.body as string) || "{}");
        if (token === "admin-tok") return new Response(JSON.stringify({ user_id: 1, email: "dmitry@vexa.ai" }), { status: 200 });
        if (token === "user-tok") return new Response(JSON.stringify({ user_id: 2, email: "bob@example.com" }), { status: 200 });
        return new Response("Invalid token", { status: 401 });
      }
      if (url.includes("/api/admin/overview")) {
        return new Response(JSON.stringify({ workloads: [], meetings: [] }), { status: 200 });
      }
      if (url.includes("/api/admin/probe")) {
        return new Response(JSON.stringify({ status: "pass", stages: [], duration_ms: 1, at: 0 }), { status: 200 });
      }
      return new Response("nope", { status: 500 });
    }),
  );
  return calls;
}

beforeEach(() => {
  cookieJar = {};
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  process.env.VEXA_ADMIN_EMAILS = "dmitry@vexa.ai, Other@Vexa.ai";
  process.env.AGENT_API_URL = "http://agent.test";
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete process.env.VEXA_ADMIN_EMAILS;
});

describe("admin gate — verified allowlist, fail-closed", () => {
  it("allowlisted admin passes with the VALIDATED email (case-insensitive)", async () => {
    cookieJar = { "vexa-token": "admin-tok" };
    const calls = stubValidate();
    const admin = await requireAdmin();
    expect(admin?.email).toBe("dmitry@vexa.ai");
    expect(calls[0].secret).toBe("internal-secret");
  });

  it("a forged user-info cookie claiming an admin email gets nothing — only the token's validated identity counts", async () => {
    cookieJar = { "vexa-token": "user-tok", "vexa-user-info": JSON.stringify({ email: "dmitry@vexa.ai" }) };
    stubValidate();
    expect(await requireAdmin()).toBeNull();
  });

  it("no allowlist configured → closed for everyone (validate never called)", async () => {
    delete process.env.VEXA_ADMIN_EMAILS;
    cookieJar = { "vexa-token": "admin-tok" };
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    expect(await requireAdmin()).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("oracle unreachable → closed", async () => {
    cookieJar = { "vexa-token": "admin-tok" };
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    expect(await requireAdmin()).toBeNull();
  });

  it("/api/admin/me: 200 for admin, plain 404 for others", async () => {
    cookieJar = { "vexa-token": "admin-tok" };
    stubValidate();
    expect((await meRoute()).status).toBe(200);

    cookieJar = { "vexa-token": "user-tok" };
    stubValidate();
    expect((await meRoute()).status).toBe(404);

    cookieJar = {};
    stubValidate();
    expect((await meRoute()).status).toBe(404);
  });

  it("/api/admin/overview: proxies for admin with the internal secret; 404 for non-admin, agent-api never called", async () => {
    cookieJar = { "vexa-token": "admin-tok" };
    const calls = stubValidate();
    const res = await overviewRoute();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ workloads: [], meetings: [] });
    const proxied = calls.find((c) => c.url.includes("/api/admin/overview"));
    expect(proxied?.url).toBe("http://agent.test/api/admin/overview");
    expect(proxied?.secret).toBe("internal-secret");

    cookieJar = { "vexa-token": "user-tok" };
    const calls2 = stubValidate();
    expect((await overviewRoute()).status).toBe(404);
    expect(calls2.some((c) => c.url.includes("/api/admin/overview"))).toBe(false);
  });

  it("/api/admin/probe: runs for admin (POST with the internal secret); 404 for non-admin, agent-api never called", async () => {
    cookieJar = { "vexa-token": "admin-tok" };
    const calls = stubValidate();
    const res = await probeRoute();
    expect(res.status).toBe(200);
    expect((await res.json()).status).toBe("pass");
    const proxied = calls.find((c) => c.url.includes("/api/admin/probe"));
    expect(proxied?.url).toBe("http://agent.test/api/admin/probe");
    expect(proxied?.secret).toBe("internal-secret");

    cookieJar = { "vexa-token": "user-tok" };
    const calls2 = stubValidate();
    expect((await probeRoute()).status).toBe(404);
    expect(calls2.some((c) => c.url.includes("/api/admin/probe"))).toBe(false);
  });
});
