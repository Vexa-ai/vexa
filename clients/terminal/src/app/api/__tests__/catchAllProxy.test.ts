import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The catch-all proxy must pass upstream statuses through faithfully — including the null-body
 *  statuses (204/205/304), where `new Response(body, …)` throws in undici. Before the fix, a
 *  successful DELETE /api/meetings/{id} (meeting-api → 204) surfaced to the browser as 502. */

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "vexa-token" ? { name, value: "alice-tok" } : undefined),
  }),
}));

import { DELETE as deleteRoute, GET as getRoute } from "../[...path]/route";

function makeReq(method: string, search = ""): import("next/server").NextRequest {
  return {
    method,
    nextUrl: { search },
    text: async () => "",
  } as unknown as import("next/server").NextRequest;
}

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_TERMINAL_MODE;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("catch-all proxy — upstream status passthrough", () => {
  it("forwards a bodyless 204 (successful DELETE) as 204, not 502", async () => {
    const fetchSpy = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchSpy);

    const res = await deleteRoute(makeReq("DELETE"), ctx("meetings", "47"));
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
    // …and the request really went to the gateway meetings root with the user's key.
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/meetings/47");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("alice-tok");
  });

  it.each([205, 304])("forwards the other null-body statuses (%i) without a body", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status })));
    const res = await getRoute(makeReq("GET"), ctx("meetings"));
    expect(res.status).toBe(status);
    expect(await res.text()).toBe("");
  });

  it("still forwards a normal JSON response body + status untouched", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })));
    const res = await getRoute(makeReq("GET"), ctx("meetings"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("returns 502 only when the upstream is actually unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await deleteRoute(makeReq("DELETE"), ctx("meetings", "47"));
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("upstream_unreachable");
  });
});
