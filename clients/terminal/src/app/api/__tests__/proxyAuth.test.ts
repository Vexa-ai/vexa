import { afterEach, describe, expect, it, vi } from "vitest";

/** Mutable cookie jar the mocked next/headers reads from. */
let jar = new Map<string, string>();

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => {
      const value = jar.get(name);
      return value === undefined ? undefined : { name, value };
    },
  }),
}));

import { resolveApiKey } from "../proxyAuth";
import { GET as catchAllGet } from "../[...path]/route";

function makeReq(search = ""): import("next/server").NextRequest {
  return { method: "GET", nextUrl: { search } } as unknown as import("next/server").NextRequest;
}

afterEach(() => {
  jar = new Map();
  delete process.env.VEXA_API_KEY;
  delete process.env.VEXA_BOT_API_KEY;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("resolveApiKey — cookie token with env fallback", () => {
  it("prefers the vexa-token cookie", async () => {
    jar.set("vexa-token", "user-token-123");
    process.env.VEXA_API_KEY = "env-key";
    process.env.VEXA_BOT_API_KEY = "bot-key";
    expect(await resolveApiKey()).toBe("user-token-123");
  });

  it("falls back to VEXA_API_KEY, then VEXA_BOT_API_KEY", async () => {
    process.env.VEXA_API_KEY = "env-key";
    process.env.VEXA_BOT_API_KEY = "bot-key";
    expect(await resolveApiKey()).toBe("env-key");

    delete process.env.VEXA_API_KEY;
    expect(await resolveApiKey()).toBe("bot-key");

    delete process.env.VEXA_BOT_API_KEY;
    expect(await resolveApiKey()).toBe("");
  });
});

describe("catch-all proxy — meetings domain forwards the cookie token as X-API-Key", () => {
  it("injects the cookie token on a /bots call", async () => {
    jar.set("vexa-token", "cookie-tok");
    const seen: { url?: string; key?: string } = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        seen.url = url;
        seen.key = (init?.headers as Record<string, string>)?.["X-API-Key"];
        return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );

    await catchAllGet(makeReq(), { params: Promise.resolve({ path: ["bots"] }) });

    expect(seen.url).toContain("/bots");
    expect(seen.key).toBe("cookie-tok");
  });

  it("falls back to VEXA_BOT_API_KEY when no cookie is present", async () => {
    process.env.VEXA_BOT_API_KEY = "legacy-bot-key";
    const seen: { key?: string } = {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        seen.key = (init?.headers as Record<string, string>)?.["X-API-Key"];
        return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );

    await catchAllGet(makeReq(), { params: Promise.resolve({ path: ["meetings"] }) });

    expect(seen.key).toBe("legacy-bot-key");
  });
});
