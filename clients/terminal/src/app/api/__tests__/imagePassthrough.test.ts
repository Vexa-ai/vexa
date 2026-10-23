import { afterEach, describe, expect, it, vi } from "vitest";

/** An image the server answers — a person's photo (`/api/people/{id}/photo`), a workspace's image, a
 *  company logo — reaches the browser as the same bytes, with its type and a PRIVATE cache. The proxy
 *  used to read every body as text and relabel it JSON, which mangles an image (UI 15g). */

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "vexa-token" ? { name, value: "alice-tok" } : undefined) }),
}));

import { GET as getRoute } from "../[...path]/route";

const req = { method: "GET", nextUrl: { search: "" }, text: async () => "" } as unknown as import("next/server").NextRequest;
const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const BYTES = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0xff, 0x00, 0xfe, 0x80]);

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("catch-all proxy — images", () => {
  it("passes a person's photo through byte for byte, privately cached", async () => {
    const fetchSpy = vi.fn(async () => new Response(BYTES, { status: 200, headers: { "Content-Type": "image/png", ETag: '"abc"' } }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await getRoute(req, ctx("people", "12", "photo"));
    expect(res.status).toBe(200);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(BYTES);
    expect(res.headers.get("content-type")).toBe("image/png");
    expect(res.headers.get("cache-control")).toMatch(/^private/);
    // a face outlives the reason to see it by at most a minute (30 s here + 30 s in agent-api)
    expect(Number(/max-age=(\d+)/.exec(res.headers.get("cache-control") ?? "")?.[1])).toBeLessThanOrEqual(30);
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("etag")).toBe('"abc"');
    const [url] = fetchSpy.mock.calls[0] as unknown as [string];
    expect(url).toContain("/agent/people/12/photo");
  });

  it("never passes an SVG through as an image", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<svg><script>1</script></svg>", { status: 200, headers: { "Content-Type": "image/svg+xml" } })));
    const res = await getRoute(req, ctx("people", "12", "photo"));
    expect(res.headers.get("content-type")).not.toMatch(/svg/);
  });

  it("a workspace's image may be an SVG — the server sanitised it; it goes through sandboxed", async () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24"/></svg>';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(svg, { status: 200, headers: { "Content-Type": "image/svg+xml" } })));
    const res = await getRoute(req, ctx("workspaces", "garden-x1", "identity", "icon"));
    expect(res.headers.get("content-type")).toBe("image/svg+xml");
    expect(res.headers.get("content-security-policy")).toContain("sandbox");
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(await res.text()).toBe(svg);
  });

  it("an SVG from any other route still does not pass as an image", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<svg/>", { status: 200, headers: { "Content-Type": "image/svg+xml" } })));
    for (const path of [["workspaces", "a", "identity", "state"], ["home", "media", "d:x.example"], ["workspaces", "a", "b", "identity", "icon"]]) {
      const res = await getRoute(req, ctx(...path));
      expect(res.headers.get("content-type")).not.toMatch(/svg/);
    }
  });

  it("a 404 (no photo, or not yours to see) stays a 404", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "no photo" }), { status: 404, headers: { "Content-Type": "application/json" } })));
    const res = await getRoute(req, ctx("people", "99", "photo"));
    expect(res.status).toBe(404);
  });
});
