import { afterEach, describe, expect, it, vi } from "vitest";

/** P18 at the chat proxy. The founder's "hi" on the 0.13.2 demo stack: agent-api answered 500, this
 *  proxy folded the body — the literal text "Internal Server Error" — into the stream, and that is
 *  what the chat showed for a runtime that had refused to start his agent. Now agent-api answers a
 *  typed `{detail, fault}`, this proxy carries the fault into the stream's `error` event, and a 5xx
 *  that names nothing still names WHO did not answer. */

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: () => undefined }),
}));

import { POST } from "../chat/route";

function req(): import("next/server").NextRequest {
  return {
    text: async () => JSON.stringify({ prompt: "hi", session: "main" }),
    headers: new Headers(),
    signal: new AbortController().signal,
  } as unknown as import("next/server").NextRequest;
}

async function firstEvent(res: Response): Promise<Record<string, unknown>> {
  const line = (await res.text()).split("\n").find((l) => l.startsWith("data: "));
  return JSON.parse((line ?? "data: {}").slice(6));
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const SPAWN_REFUSED = {
  source: "runtime", kind: "spawn_refused", op: "spawn", status: 502,
  detail: "a previous agent for this chat is still registered",
  remedy: "Retry in a few seconds; if it keeps happening, an operator must remove the stale agent workload.",
};

describe("chat proxy — typed faults reach the stream", () => {
  it("carries agent-api's typed refusal into the `error` event", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "The agent runtime could not start your agent: a previous agent for this chat is still registered.",
                       fault: SPAWN_REFUSED }), { status: 502, headers: { "Content-Type": "application/json" } })));
    const ev = await firstEvent(await POST(req()));
    expect(ev.type).toBe("error");
    expect(ev.status).toBe(502);
    expect(ev.fault).toEqual(SPAWN_REFUSED);
    expect(String(ev.message)).toContain("a previous agent for this chat is still registered");
  });

  it("never passes a bare 'Internal Server Error' through — it names the service that failed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Internal Server Error", { status: 500 })));
    const ev = await firstEvent(await POST(req()));
    expect(JSON.stringify(ev)).not.toContain("Internal Server Error");
    expect(ev.fault).toMatchObject({ source: "agent-api", kind: "internal", status: 500 });
  });

  it("keeps a sentence the service wrote itself, typed as the agent service's", async () => {
    const said = "That message did not reach your agent — nothing was lost on your side, please send it again.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: said }), { status: 503 })));
    const ev = await firstEvent(await POST(req()));
    expect(ev.message).toBe(said);
    expect(ev.fault).toMatchObject({ source: "agent-api", kind: "unavailable", status: 503, detail: said });
  });

  it("a gateway that cannot be reached is named as the gateway", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const ev = await firstEvent(await POST(req()));
    expect(ev.fault).toMatchObject({ source: "gateway", kind: "unreachable" });
  });

  it("leaves a 4xx refusal (the session) exactly as it was", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "invalid token" }), { status: 401 })));
    const ev = await firstEvent(await POST(req()));
    expect(ev.status).toBe(401);
    expect(ev.fault).toBeUndefined();
  });
});
