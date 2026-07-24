import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/auth-utils", () => ({
  getAuthenticatedUserIdentity: vi.fn().mockResolvedValue({
    userId: 41,
    email: "person@example.com",
  }),
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

  it("lists secret-free token metadata for the token-bound user ID", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
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
      `${ADMIN_URL}/admin/users/41/tokens`,
    ]);
    expect(JSON.stringify(calls)).not.toContain("vxa_");
  });

  it("mints with exactly the stock-supported JSON fields", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
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
          scopes: ["bot", "tx"],
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
      `${ADMIN_URL}/admin/users/41/tokens`,
    ]);
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      scopes: ["bot", "tx"],
      name: "automation",
      expires_in: 3600,
    });
  });

  it.each([
    ["email", { scopes: ["bot"], email: "attacker@example.net" }],
    ["userId", { scopes: ["bot"], userId: "999" }],
    ["scope alias", { scope: ["bot"] }],
    ["string scopes", { scopes: "bot,tx" }],
    ["non-string scope", { scopes: ["bot", 7] }],
    ["non-string name", { scopes: ["bot"], name: 7 }],
    ["string expiration", { scopes: ["bot"], expires_in: "3600" }],
    ["non-positive expiration", { scopes: ["bot"], expires_in: 0 }],
    ["unknown field", { scopes: ["bot"], unexpected: true }],
  ])("rejects %s before any Admin request", async (_case, body) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);

    const response = await POST(
      new NextRequest("http://dashboard.test/api/profile/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({
      error: { code: "VALIDATION_ERROR" },
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("preserves a structured FastAPI 422 as a typed actionable response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
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
        body: JSON.stringify({ scopes: ["bot"], name: "automation" }),
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
      vi.fn(async () => {
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
