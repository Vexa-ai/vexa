import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cookieValues = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) =>
      cookieValues.has(name) ? { name, value: cookieValues.get(name)! } : undefined,
  }),
}));

import { getAuthenticatedUser, getAuthenticatedUserId } from "@/lib/auth-utils";

let savedApiUrl: string | undefined;

beforeEach(() => {
  savedApiUrl = process.env.VEXA_API_URL;
  process.env.VEXA_API_URL = "http://gateway.test";
});

afterEach(() => {
  if (savedApiUrl === undefined) delete process.env.VEXA_API_URL;
  else process.env.VEXA_API_URL = savedApiUrl;
  cookieValues.clear();
  vi.unstubAllGlobals();
});

describe("getAuthenticatedUser", () => {
  it("resolves the user from the token via the gateway's /auth/me", async () => {
    cookieValues.set("vexa-token", "user-token");
    const fetchSpy = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify({ user_id: 42, email: "user@example.com" }))
    );
    vi.stubGlobal("fetch", fetchSpy);

    expect(await getAuthenticatedUser()).toEqual({ id: "42", email: "user@example.com" });
    expect(await getAuthenticatedUserId()).toBe("42");
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://gateway.test/auth/me");
    expect((init?.headers as Record<string, string>)["X-API-Key"]).toBe("user-token");
  });

  it("ignores the user-info cookie when deciding who the user is", async () => {
    cookieValues.set("vexa-token", "attacker-token");
    cookieValues.set("vexa-user-info", JSON.stringify({ email: "victim@example.com" }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ user_id: 7, email: "attacker@example.com" })))
    );
    expect(await getAuthenticatedUser()).toEqual({ id: "7", email: "attacker@example.com" });
  });

  it("returns null without a token, for an invalid token, or for a malformed answer", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    expect(await getAuthenticatedUser()).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();

    cookieValues.set("vexa-token", "bad-token");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    expect(await getAuthenticatedUser()).toBeNull();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ user_id: "../users", email: "x" })))
    );
    expect(await getAuthenticatedUser()).toBeNull();

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      })
    );
    expect(await getAuthenticatedUser()).toBeNull();
  });
});
