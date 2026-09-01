import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The workspace proxy is the more specific route, so it SHADOWS src/app/api/[...path]/route.ts for
 *  every /api/workspace/* path. Next answers a method this file does not export with 405 — it does
 *  NOT fall through to the catch-all that does export it. That is exactly how the pages panel's
 *  Edit → Save broke: writeWorkspaceFile() PUTs /api/workspace/file, this file exported only
 *  GET/POST/DELETE, and every save came back "Could not save: /api/workspace/file → 405".
 *
 *  So the first thing asserted here is EXPORT PRESENCE — the shape of the bug — and the rest is that
 *  PUT forwards like its siblings and that meetings-mode still refuses workspace WRITES. */

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "vexa-token" ? { name, value: "alice-tok" } : undefined),
  }),
}));

import * as workspaceRoute from "../workspace/[...seg]/route";
import { DELETE as deleteRoute, GET as getRoute, POST as postRoute, PUT as putRoute } from "../workspace/[...seg]/route";

function makeReq(method: string, search = ""): import("next/server").NextRequest {
  return {
    method,
    nextUrl: { search },
    body: null,
    headers: new Headers({ "Content-Type": "application/json" }),
    text: async () => "",
  } as unknown as import("next/server").NextRequest;
}

const ctx = (...seg: string[]) => ({ params: Promise.resolve({ seg }) });

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_TERMINAL_MODE;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("workspace proxy — every method the surface uses is exported here", () => {
  // A shadowing route file is only as complete as its export list; anything missing is a 405.
  it.each(["GET", "POST", "PUT", "DELETE"])("exports %s", (method) => {
    expect(typeof (workspaceRoute as unknown as Record<string, unknown>)[method]).toBe("function");
  });

  it("keeps the four handlers distinct (no accidental re-export of one for another)", () => {
    expect(new Set([getRoute, postRoute, putRoute, deleteRoute]).size).toBe(4);
  });
});

describe("workspace proxy — PUT (the pages panel's Edit → Save)", () => {
  it("forwards a doc write to the gateway's agent branch with the caller's key", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ path: "README.md", written: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const res = await putRoute(makeReq("PUT"), ctx("file"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ path: "README.md", written: true });

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/agent/workspace/file");
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("alice-tok");
  });

  it("carries the query string through, like the reads do", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    await putRoute(makeReq("PUT", "?slug=wsA"), ctx("file"));
    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/agent/workspace/file?slug=wsA");
  });

  // The authz distinction that 405 was hiding: agent-api refuses a non-admin's _global write with
  // 403 ("only an org admin may edit _global"). 403 is "you may not"; 405 was "there is no door".
  it("passes an upstream 403 through as 403 — never masked as 405 or 502", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "only an org admin may edit _global" }), { status: 403 })));
    const res = await putRoute(makeReq("PUT"), ctx("file"));
    expect(res.status).toBe(403);
    expect((await res.json()).detail).toContain("_global");
  });

  it("returns 502 when the gateway is unreachable (fail loud, never a silent ok)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await putRoute(makeReq("PUT"), ctx("file"));
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("upstream_unavailable");
  });
});

describe("workspace proxy — meetings-only mode keeps refusing WRITES while READS stay open", () => {
  it("refuses PUT with 404 and never reaches the upstream", async () => {
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const res = await putRoute(makeReq("PUT"), ctx("file"));
    expect(res.status).toBe(404);
    expect((await res.json()).error).toBe("not_found");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("still allows the composed deep-link READ (GET) in the same mode", async () => {
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "meetings";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ content: "# doc" }), { status: 200 })));
    const res = await getRoute(makeReq("GET", "?path=README.md"), ctx("file"));
    expect(res.status).toBe(200);
  });

  it("minutes mode is NOT meetings mode — a doc write there goes upstream", async () => {
    process.env.NEXT_PUBLIC_TERMINAL_MODE = "minutes";
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ written: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await putRoute(makeReq("PUT"), ctx("file"));
    expect(res.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledOnce();
  });
});
