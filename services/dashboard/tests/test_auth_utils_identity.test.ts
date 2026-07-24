import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieGet = vi.fn();

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({ get: cookieGet })),
}));

import {
  getAuthenticatedUserId,
  getAuthenticatedUserIdentity,
} from "@/lib/auth-utils";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("token-bound hosted identity", () => {
  beforeEach(() => {
    process.env.VEXA_API_URL = "http://stock-gateway.test";
    cookieGet.mockReset();
    cookieGet.mockImplementation((name: string) => {
      if (name === "vexa-token") return { value: "token-owner-a" };
      if (name === "vexa-user-info") {
        return {
          value: JSON.stringify({
            email: "forged-owner-b@example.net",
            id: 999,
          }),
        };
      }
      return undefined;
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    delete process.env.VEXA_API_URL;
  });

  it("uses only /auth/me identity when a stale user-info cookie names another user", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        calls.push({ url: String(input), init });
        return jsonResponse({
          user_id: 41,
          email: "owner-a@example.com",
          scopes: ["bot"],
          max_concurrent: 3,
        });
      })
    );

    await expect(getAuthenticatedUserIdentity()).resolves.toEqual({
      userId: 41,
      email: "owner-a@example.com",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://stock-gateway.test/auth/me");
    expect(calls[0].init?.headers).toEqual({
      "X-API-Key": "token-owner-a",
    });
    expect(cookieGet).toHaveBeenCalledTimes(1);
    expect(cookieGet).toHaveBeenCalledWith("vexa-token");
    expect(JSON.stringify(calls)).not.toContain("forged-owner-b");
  });

  it("keeps existing user-ID consumers on the same token-bound identity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          user_id: 41,
          email: "owner-a@example.com",
          scopes: ["bot"],
          max_concurrent: 3,
        })
      )
    );

    await expect(getAuthenticatedUserId()).resolves.toBe("41");
    expect(cookieGet).toHaveBeenCalledTimes(1);
    expect(cookieGet).not.toHaveBeenCalledWith("vexa-user-info");
  });

  it.each([
    [{ user_id: "41", email: "owner-a@example.com" }],
    [{ user_id: 41, email: "" }],
    [{ user_id: -1, email: "owner-a@example.com" }],
  ])("rejects malformed /auth/me identity %#", async (body) => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(body)));
    await expect(getAuthenticatedUserIdentity()).resolves.toBeNull();
  });
});
