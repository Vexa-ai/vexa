import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NextRequest } from "next/server";

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "vexa-token" ? { name, value: "alice-tok" } : undefined),
  }),
}));

import { DELETE } from "../route";

function makeReq(search = ""): NextRequest {
  return { nextUrl: { search }, headers: new Headers() } as unknown as NextRequest;
}

const ctx = (...seg: string[]) => ({ params: Promise.resolve({ seg }) });

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_TERMINAL_MODE;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("workspace proxy DELETE", () => {
  const deleteCases: Array<[string, string[], string, string]> = [
    ["workspace deletion", ["workspace-1"], "", "/agent/workspace/workspace-1"],
    ["member removal", ["members", "user-2"], "?workspace_id=workspace-1", "/agent/workspace/members/user-2?workspace_id=workspace-1"],
    ["invite revocation", ["invites", "invite-3"], "?workspace_id=workspace-1", "/agent/workspace/invites/invite-3?workspace_id=workspace-1"],
  ];

  it.each(deleteCases)("forwards %s to the gateway", async (_name, seg, search, expectedPath) => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchSpy);

    const res = await DELETE(makeReq(search), ctx(...seg));

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain(expectedPath);
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("alice-tok");
  });

  it("preserves an upstream failure response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    })));

    const res = await DELETE(makeReq(), ctx("workspace-1"));

    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ detail: "not found" });
  });
});
