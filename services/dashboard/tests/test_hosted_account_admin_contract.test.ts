import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/auth-utils", () => ({
  getAuthenticatedUserEmail: vi.fn().mockResolvedValue("person@example.com"),
  getAuthenticatedUserId: vi.fn().mockResolvedValue("41"),
}));

import { GET, POST } from "@/app/api/profile/keys/route";

const ADMIN_URL = "http://stock-admin.test";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("hosted Account adapter against the stock v0.12.18 Admin API contract", () => {
  beforeEach(() => {
    process.env.VEXA_ADMIN_API_URL = ADMIN_URL;
    process.env.VEXA_ADMIN_API_KEY = "test-admin-key";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    delete process.env.VEXA_ADMIN_API_URL;
    delete process.env.VEXA_ADMIN_API_KEY;
  });

  it("resolves the authenticated email, then lists secret-free token metadata", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (url === `${ADMIN_URL}/admin/users/email/person%40example.com`) {
          return jsonResponse({
            id: 41,
            email: "person@example.com",
            name: "Person",
            max_concurrent_bots: 3,
          });
        }
        if (url === `${ADMIN_URL}/admin/users/41/tokens`) {
          return jsonResponse([
            {
              id: 7,
              user_id: 41,
              scopes: ["bot", "tx"],
              name: "automation",
              created_at: "2026-07-24T08:00:00Z",
              last_used_at: null,
              expires_at: null,
            },
          ]);
        }
        // Exact stock behavior: user-by-id is not a route.
        if (url === `${ADMIN_URL}/admin/users/41`) {
          return jsonResponse({ detail: "Not Found" }, 404);
        }
        throw new Error(`unexpected request: ${url}`);
      })
    );

    const response = await GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      keys: [
        {
          id: "7",
          scopes: ["bot", "tx"],
          name: "automation",
          created_at: "2026-07-24T08:00:00Z",
          last_used_at: null,
          expires_at: null,
        },
      ],
    });
    expect(calls.map((call) => call.url)).toEqual([
      `${ADMIN_URL}/admin/users/email/person%40example.com`,
      `${ADMIN_URL}/admin/users/41/tokens`,
    ]);
    expect(JSON.stringify(calls)).not.toContain("vxa_");
  });

  it("upserts a newly authenticated account only after the supported email lookup returns 404", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (url === `${ADMIN_URL}/admin/users/email/person%40example.com`) {
          return jsonResponse({ detail: "User not found" }, 404);
        }
        if (url === `${ADMIN_URL}/admin/users`) {
          return jsonResponse(
            {
              id: 41,
              email: "person@example.com",
              name: null,
              max_concurrent_bots: 3,
            },
            201
          );
        }
        if (url === `${ADMIN_URL}/admin/users/41/tokens`) {
          return jsonResponse([]);
        }
        throw new Error(`unexpected request: ${url}`);
      })
    );

    const response = await GET();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ keys: [] });
    expect(calls.map((call) => call.url)).toEqual([
      `${ADMIN_URL}/admin/users/email/person%40example.com`,
      `${ADMIN_URL}/admin/users`,
      `${ADMIN_URL}/admin/users/41/tokens`,
    ]);
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      email: "person@example.com",
    });
  });

  it("mints with only stock-supported JSON fields and never forwards client identity", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (url === `${ADMIN_URL}/admin/users/email/person%40example.com`) {
          return jsonResponse({
            id: 41,
            email: "person@example.com",
            name: "Person",
            max_concurrent_bots: 3,
          });
        }
        if (url === `${ADMIN_URL}/admin/users/41/tokens`) {
          return jsonResponse(
            {
              id: 8,
              token: "vxa_bot_one-time-secret",
              user_id: 41,
              scopes: ["bot", "tx"],
            },
            201
          );
        }
        throw new Error(`unexpected request: ${url}`);
      })
    );

    const response = await POST(
      new NextRequest("http://dashboard.test/api/profile/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: "attacker@example.net",
          userId: "999",
          scopes: "bot,tx",
          name: "automation",
          expires_in: 3600,
        }),
      })
    );

    expect(response.status).toBe(201);
    expect(await response.json()).toMatchObject({
      id: 8,
      token: "vxa_bot_one-time-secret",
      scopes: ["bot", "tx"],
    });
    expect(calls.map((call) => call.url)).toEqual([
      `${ADMIN_URL}/admin/users/email/person%40example.com`,
      `${ADMIN_URL}/admin/users/41/tokens`,
    ]);
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      scopes: ["bot", "tx"],
      name: "automation",
      expires_in: 3600,
    });
  });

  it("preserves a structured FastAPI 422 as a typed actionable response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url.includes("/admin/users/email/")) {
          return jsonResponse({
            id: 41,
            email: "person@example.com",
            name: "Person",
            max_concurrent_bots: 3,
          });
        }
        return jsonResponse(
          {
            detail: [
              {
                type: "extra_forbidden",
                loc: ["body", "userId"],
                msg: "Extra inputs are not permitted",
                input: "999",
              },
            ],
          },
          422
        );
      })
    );

    const response = await POST(
      new NextRequest("http://dashboard.test/api/profile/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scopes: "bot", name: "automation" }),
      })
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({
      error: {
        code: "VALIDATION_ERROR",
        message: "body.userId: Extra inputs are not permitted",
      },
    });
  });

  it("does not log the one-time token returned by a successful mint", async () => {
    const logSpies = [
      vi.spyOn(console, "log").mockImplementation(() => undefined),
      vi.spyOn(console, "info").mockImplementation(() => undefined),
      vi.spyOn(console, "warn").mockImplementation(() => undefined),
      vi.spyOn(console, "error").mockImplementation(() => undefined),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        if (url.includes("/admin/users/email/")) {
          return jsonResponse({
            id: 41,
            email: "person@example.com",
            name: "Person",
            max_concurrent_bots: 3,
          });
        }
        return jsonResponse(
          {
            id: 8,
            token: "vxa_bot_one-time-secret",
            user_id: 41,
            scopes: ["bot"],
          },
          201
        );
      })
    );

    const response = await POST(
      new NextRequest("http://dashboard.test/api/profile/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scopes: ["bot"], name: "automation" }),
      })
    );

    expect(response.status).toBe(201);
    expect((await response.json()).token).toBe("vxa_bot_one-time-secret");
    for (const spy of logSpies) {
      expect(spy).not.toHaveBeenCalled();
    }
  });
});
