import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  cookies: vi.fn(async () => ({
    get: vi.fn(() => ({ value: "user-api-token" })),
  })),
}));

const getAuthenticatedUserId = vi.fn();
vi.mock("@/lib/auth-utils", () => ({
  getAuthenticatedUserId: () => getAuthenticatedUserId(),
}));

async function loadRoute() {
  vi.resetModules();
  process.env.VEXA_ADMIN_API_URL = "http://admin-api.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-secret";
  return import("../src/app/api/profile/keys/[id]/route");
}

describe("profile API key deletion authorization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    getAuthenticatedUserId.mockReset();
  });

  it("does not delete API keys that are absent from the authenticated user's token list", async () => {
    getAuthenticatedUserId.mockResolvedValue("user-1");
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "http://admin-api.test/admin/users/user-1" && !init?.method) {
        return new Response(
          JSON.stringify({ api_tokens: [{ id: 111, token: "owned-token", created_at: "2026-01-01T00:00:00Z" }] }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { DELETE } = await loadRoute();

    const response = await DELETE(
      new Request("http://dashboard.test/api/profile/keys/222") as never,
      { params: Promise.resolve({ id: "222" }) }
    );

    expect(response.status).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalledWith(
      "http://admin-api.test/admin/tokens/222",
      expect.anything()
    );
  });
});
