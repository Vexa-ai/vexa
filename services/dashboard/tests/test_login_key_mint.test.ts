import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createUserToken } from "@/lib/vexa-admin-api";

// Every sign-in (nextauth, send-magic-link, verify) mints the key its cookie carries through
// createUserToken. It must be a named, ending SYSTEM credential (admin-api MIGRATION-0014), never
// an unnamed key with no end that piles up in the person's key list (#1868).

beforeEach(() => {
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
});

afterEach(() => {
  delete process.env.VEXA_ADMIN_API_URL;
  delete process.env.VEXA_ADMIN_API_KEY;
  vi.unstubAllGlobals();
});

describe("createUserToken", () => {
  it("mints a named system key that ends with its 30-day cookie", async () => {
    const fetchSpy = vi.fn(async (_url: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ id: 1, token: "vxa_bot_x", user_id: 7, scopes: ["bot"] }), { status: 201 }));
    vi.stubGlobal("fetch", fetchSpy);

    const result = await createUserToken("7");
    expect(result.success).toBe(true);
    const url = new URL(fetchSpy.mock.calls[0][0]);
    expect(url.pathname).toBe("/admin/users/7/tokens");
    expect(url.searchParams.get("name")).toBe("dashboard-login");
    expect(url.searchParams.get("credential_class")).toBe("system");
    expect(url.searchParams.get("expires_in")).toBe(String(60 * 60 * 24 * 30));
    expect(fetchSpy.mock.calls[0][1]?.method).toBe("POST");
  });
});
