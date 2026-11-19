import { afterEach, describe, expect, it, vi } from "vitest";

/** A chat attachment's bytes come back as bytes — through the gateway, on the person's own key, with
 *  agent-api's headers. The catch-all would decode a PNG as text; this route must not. */
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (n: string) => (n === "vexa-token" ? { name: n, value: "alice-tok" } : undefined) }),
}));

import { GET } from "../sessions/[session]/attachment/route";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const req = (search: string) => ({ nextUrl: { search }, headers: new Headers() }) as unknown as import("next/server").NextRequest;

describe("GET /api/sessions/:session/attachment", () => {
  it("streams the bytes through unchanged on the caller's key", async () => {
    const png = new Uint8Array([137, 80, 78, 71, 0, 255, 254]);
    const fetchMock = vi.fn(async () => new Response(png, { status: 200, headers: {
      "content-type": "image/png", "x-content-type-options": "nosniff", "content-security-policy": "default-src 'none'; sandbox" } }));
    vi.stubGlobal("fetch", fetchMock);
    const r = await GET(req("?path=assets%2F0123456789abcdef-a.png"), { params: Promise.resolve({ session: "s-1 x" }) });
    expect(r.status).toBe(200);
    expect(new Uint8Array(await r.arrayBuffer())).toEqual(png);
    expect(r.headers.get("content-type")).toBe("image/png");
    expect(r.headers.get("x-content-type-options")).toBe("nosniff");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/agent\/sessions\/s-1%20x\/attachment\?path=assets%2F0123456789abcdef-a\.png$/);
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("alice-tok");
  });

  it("passes a refusal through as the upstream's status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"detail":"not found"}', { status: 404 })));
    const r = await GET(req("?path=x"), { params: Promise.resolve({ session: "s" }) });
    expect(r.status).toBe(404);
  });
});
