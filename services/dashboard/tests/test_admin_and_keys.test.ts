import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieValues = new Map<string, string>();
const setCookies = new Map<string, string>();
const getAuthenticatedUserId = vi.fn();

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      cookieValues.has(name) ? { name, value: cookieValues.get(name)! } : undefined,
    set: (name: string, value: string) => {
      setCookies.set(name, value);
    },
  }),
}));
vi.mock("@/lib/auth-utils", () => ({
  getAuthenticatedUserId: () => getAuthenticatedUserId(),
}));

import {
  ADMIN_COOKIE_NAME,
  checkAdminSessionValue,
  createAdminSessionValue,
} from "@/lib/admin-session";

const ENV_KEYS = ["JWT_SECRET", "VEXA_ADMIN_API_URL", "VEXA_ADMIN_API_KEY"];
const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const key of ENV_KEYS) savedEnv[key] = process.env[key];
  process.env.JWT_SECRET = "jwt-secret-for-tests";
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
});

afterEach(() => {
  for (const key of ENV_KEYS) {
    if (savedEnv[key] === undefined) delete process.env[key];
    else process.env[key] = savedEnv[key];
  }
  cookieValues.clear();
  setCookies.clear();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function unsignedSession(timestamp = Date.now()): string {
  return Buffer.from(JSON.stringify({ authenticated: true, timestamp })).toString("base64");
}

describe("admin session cookie", () => {
  it("accepts a value it issued", () => {
    const value = createAdminSessionValue()!;
    expect(checkAdminSessionValue(value)).toEqual({ valid: true });
  });

  it("refuses an unsigned, re-signed, tampered or expired value", () => {
    expect(checkAdminSessionValue(undefined)).toEqual({ valid: false, reason: "missing" });
    expect(checkAdminSessionValue(unsignedSession())).toEqual({ valid: false, reason: "invalid" });
    expect(checkAdminSessionValue(`${unsignedSession()}.${"0".repeat(64)}`).valid).toBe(false);

    const value = createAdminSessionValue()!;
    const [payload, sig] = value.split(".");
    const other = Buffer.from(JSON.stringify({ authenticated: true, timestamp: Date.now() + 1 }))
      .toString("base64");
    expect(checkAdminSessionValue(`${other}.${sig}`).valid).toBe(false);
    expect(checkAdminSessionValue(`${payload}.${sig.slice(0, -1)}`).valid).toBe(false);

    process.env.JWT_SECRET = "a-different-secret";
    expect(checkAdminSessionValue(value).valid).toBe(false);

    process.env.JWT_SECRET = "jwt-secret-for-tests";
    const old = createAdminSessionValue(Date.now() - 25 * 60 * 60 * 1000)!;
    expect(checkAdminSessionValue(old)).toEqual({ valid: false, reason: "expired" });
  });

  it("issues nothing and accepts nothing without JWT_SECRET", () => {
    const value = createAdminSessionValue()!;
    delete process.env.JWT_SECRET;
    expect(createAdminSessionValue()).toBeNull();
    expect(checkAdminSessionValue(value).valid).toBe(false);
  });
});

describe("/api/admin proxy", () => {
  const params = { params: Promise.resolve({ path: ["users"] }) };

  it("refuses an unsigned session cookie without calling the admin API", async () => {
    cookieValues.set(ADMIN_COOKIE_NAME, unsignedSession());
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const { GET } = await import("@/app/api/admin/[...path]/route");
    const res = await GET(new NextRequest("https://dashboard.example.com/api/admin/users"), params);
    expect(res.status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("proxies for a signed session", async () => {
    cookieValues.set(ADMIN_COOKIE_NAME, createAdminSessionValue()!);
    const fetchSpy = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response("[]", { headers: { "content-type": "application/json" } })
    );
    vi.stubGlobal("fetch", fetchSpy);
    const { GET } = await import("@/app/api/admin/[...path]/route");
    const res = await GET(new NextRequest("https://dashboard.example.com/api/admin/users"), params);
    expect(res.status).toBe(200);
    expect(fetchSpy.mock.calls[0][0]).toBe("http://admin.test/admin/users");
  });
});

describe("/api/auth/admin-verify", () => {
  function verifyRequest(token: unknown) {
    return new NextRequest("https://dashboard.example.com/api/auth/admin-verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  }

  it("issues a signed session for the admin key, and the proxy accepts it", async () => {
    const { POST, GET } = await import("@/app/api/auth/admin-verify/route");
    const res = await POST(verifyRequest("admin-key"));
    expect(res.status).toBe(200);
    const issued = setCookies.get(ADMIN_COOKIE_NAME)!;
    expect(checkAdminSessionValue(issued)).toEqual({ valid: true });

    cookieValues.set(ADMIN_COOKIE_NAME, issued);
    expect((await GET()).status).toBe(200);
  });

  it("refuses a wrong key", async () => {
    const { POST } = await import("@/app/api/auth/admin-verify/route");
    expect((await POST(verifyRequest("admin-kez"))).status).toBe(401);
    expect((await POST(verifyRequest("admin"))).status).toBe(401);
    expect(setCookies.size).toBe(0);
  });

  it("reports an unsigned cookie as invalid", async () => {
    cookieValues.set(ADMIN_COOKIE_NAME, unsignedSession());
    const { GET } = await import("@/app/api/auth/admin-verify/route");
    const res = await GET();
    expect(res.status).toBe(401);
  });
});

describe("DELETE /api/profile/keys/:id", () => {
  const params = (id: string) => ({ params: Promise.resolve({ id }) });
  const request = () =>
    new NextRequest("https://dashboard.example.com/api/profile/keys/1", { method: "DELETE" });

  function stubAdmin() {
    const fetchSpy = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url === "http://admin.test/admin/users/42") {
        return new Response(JSON.stringify({ id: 42, api_tokens: [{ id: 11 }, { id: 12 }] }));
      }
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetchSpy);
    return fetchSpy;
  }

  it("revokes a key the signed-in user owns", async () => {
    getAuthenticatedUserId.mockResolvedValue("42");
    const fetchSpy = stubAdmin();
    const { DELETE } = await import("@/app/api/profile/keys/[id]/route");
    const res = await DELETE(request(), params("12"));
    expect(res.status).toBe(200);
    expect(fetchSpy.mock.calls.map((c) => [c[0], c[1]?.method ?? "GET"])).toEqual([
      ["http://admin.test/admin/users/42", "GET"],
      ["http://admin.test/admin/tokens/12", "DELETE"],
    ]);
  });

  it("does not revoke someone else's key", async () => {
    getAuthenticatedUserId.mockResolvedValue("42");
    const fetchSpy = stubAdmin();
    const { DELETE } = await import("@/app/api/profile/keys/[id]/route");
    const res = await DELETE(request(), params("99"));
    expect(res.status).toBe(404);
    expect(fetchSpy.mock.calls.some((c) => c[1]?.method === "DELETE")).toBe(false);
  });

  it("refuses without a session and rejects non-numeric ids", async () => {
    const fetchSpy = stubAdmin();
    const { DELETE } = await import("@/app/api/profile/keys/[id]/route");
    getAuthenticatedUserId.mockResolvedValue(null);
    expect((await DELETE(request(), params("12"))).status).toBe(401);
    getAuthenticatedUserId.mockResolvedValue("42");
    expect((await DELETE(request(), params("../users/1"))).status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
