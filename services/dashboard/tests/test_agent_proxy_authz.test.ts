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

function mockFetchJson(status = 200, body: unknown = { ok: true }) {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function loadAgentRoute() {
  vi.resetModules();
  process.env.AGENT_API_URL = "http://agent-api.test";
  process.env.AGENT_API_TOKEN = "service-token";
  return import("../src/app/api/agent/[...path]/route");
}

describe("dashboard agent proxy authorization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    getAuthenticatedUserId.mockReset();
  });

  it("returns 401 before proxying unauthenticated requests", async () => {
    getAuthenticatedUserId.mockResolvedValue(null);
    const fetchMock = mockFetchJson();
    const { GET } = await loadAgentRoute();

    const response = await GET(
      new Request("http://dashboard.test/api/agent/sessions?user_id=victim") as never,
      { params: Promise.resolve({ path: ["sessions"] }) }
    );

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("binds GET user_id query params to the authenticated dashboard user", async () => {
    getAuthenticatedUserId.mockResolvedValue("user-1");
    const fetchMock = mockFetchJson();
    const { GET } = await loadAgentRoute();

    const response = await GET(
      new Request("http://dashboard.test/api/agent/sessions?user_id=user-2&limit=10") as never,
      { params: Promise.resolve({ path: ["sessions"] }) }
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const forwardedUrl = fetchMock.mock.calls[0][0] as string;
    expect(forwardedUrl).toContain("user_id=user-1");
    expect(forwardedUrl).toContain("limit=10");
    expect(forwardedUrl).not.toContain("user_id=user-2");
  });

  it("overwrites JSON body user_id with the authenticated dashboard user", async () => {
    getAuthenticatedUserId.mockResolvedValue("user-1");
    const fetchMock = mockFetchJson();
    const { POST } = await loadAgentRoute();

    const response = await POST(
      new Request("http://dashboard.test/api/agent/schedule", {
        method: "POST",
        body: JSON.stringify({ user_id: "user-2", action: "chat", message: "hello" }),
        headers: { "Content-Type": "application/json" },
      }) as never,
      { params: Promise.resolve({ path: ["schedule"] }) }
    );

    expect(response.status).toBe(200);
    const forwarded = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(forwarded.user_id).toBe("user-1");
    expect(forwarded.message).toBe("hello");
  });
});
