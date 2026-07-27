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
    headers: new Headers(),
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

// ── media passthrough ───────────────────────────────────────────────────────────────────────────
// Recordings are BINARY and are fetched with Range requests (that is how a <video>/<audio> element
// seeks). The default JSON path — `await upstream.text()` relabelled application/json — would
// re-encode the bytes as UTF-8 and hand the player a corrupt file with no scrub bar.
describe("catch-all proxy — recordings stream as media, not as JSON text", () => {
  function mediaReq(range?: string): import("next/server").NextRequest {
    return {
      method: "GET",
      nextUrl: { search: "?type=video" },
      headers: new Headers(range ? { range } : {}),
    } as unknown as import("next/server").NextRequest;
  }

  it("forwards Range upstream and returns 206 + the range headers verbatim", async () => {
    const seen: { url?: string; range?: string } = {};
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      seen.url = url;
      seen.range = (init?.headers as Record<string, string>)?.["Range"];
      return new Response(new Uint8Array([0x1a, 0x45, 0xdf, 0xa3]), {
        status: 206,
        headers: {
          "Content-Type": "video/webm",
          "Content-Range": "bytes 0-3/1024",
          "Content-Length": "4",
        },
      });
    }));

    const res = await getRoute(mediaReq("bytes=0-3"), ctx("recordings", "42", "master"));

    // it reached the MEETINGS domain (gateway root), not /agent/*
    expect(seen.url).toContain("/recordings/42/master");
    expect(seen.url).not.toContain("/agent/");
    expect(seen.range).toBe("bytes=0-3");

    expect(res.status).toBe(206);
    expect(res.headers.get("Content-Type")).toBe("video/webm");
    expect(res.headers.get("Content-Range")).toBe("bytes 0-3/1024");
    expect(res.headers.get("Accept-Ranges")).toBe("bytes");
    // the BYTES survive — a UTF-8 round-trip would mangle 0x1a45dfa3 (the WebM magic number)
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([0x1a, 0x45, 0xdf, 0xa3]));
  });

  it("leaves a JSON response on the JSON path (no regression for normal calls)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const res = await getRoute(mediaReq(), ctx("recordings"));
    expect(res.headers.get("Content-Type")).toBe("application/json");
    expect(await res.json()).toEqual({ ok: true });
  });
});
