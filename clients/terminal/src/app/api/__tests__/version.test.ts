/** `GET /api/version` on the terminal — the two halves of the pairing rule, in one answer.
 *
 *  It is not a hop through the catch-all proxy on purpose: that route carries a per-user API key to
 *  the gateway, and the tab most likely to be running a stale bundle is one with no session at all.
 *  It also has to report the bundle's own build id, which only this process knows.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "../version/route";
import { TERMINAL_AGENT_API } from "../../../version";

const agentVersion = (body: unknown, status = 200) =>
  vi.fn(async (url: string) => { void url; return new Response(JSON.stringify(body), { status }); });

beforeEach(() => { vi.stubEnv("AGENT_API_URL", "http://agent.test"); vi.stubEnv("NEXT_PUBLIC_BUILD_ID", "line-aaaa"); });
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function body() { return (await GET()).json(); }

describe("terminal /api/version", () => {
  it("reports both halves — this bundle and the agent-api it resolves", async () => {
    vi.stubGlobal("fetch", agentVersion({ service: "agent-api", sha: "line-bbbb", api: TERMINAL_AGENT_API }));
    expect(await body()).toEqual({
      terminal: { build: "line-aaaa", agent_api: TERMINAL_AGENT_API },
      server: { sha: "line-bbbb", api: TERMINAL_AGENT_API },
      paired: true,
    });
  });

  it("calls agent-api through the network alias the swap moves", async () => {
    const f = agentVersion({ sha: "x", api: TERMINAL_AGENT_API });
    vi.stubGlobal("fetch", f);
    await body();
    expect(f.mock.calls[0]?.[0]).toBe("http://agent.test/api/version");
  });

  it("says paired:false when the server answers a contract this bundle was not built for", async () => {
    vi.stubGlobal("fetch", agentVersion({ sha: "line-bbbb", api: TERMINAL_AGENT_API + 1 }));
    const b = await body();
    expect(b.paired).toBe(false);
    expect(b.server.api).toBe(TERMINAL_AGENT_API + 1);
  });

  it("answers server:null — not a 500 — when agent-api is unreachable, a non-200, or junk", async () => {
    // A swap makes agent-api briefly unreachable by construction. A version probe that failed
    // loudly would paint an error in the client every time a container was a second slow.
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    expect((await GET()).status).toBe(200);
    expect((await body()).server).toBeNull();
    vi.stubGlobal("fetch", agentVersion({ sha: "x", api: 1 }, 503));
    expect((await body()).server).toBeNull();
    vi.stubGlobal("fetch", agentVersion({ nothing: true }));
    expect((await body()).server).toBeNull();
  });

  it("an unreachable server is PAIRED — unknown is not a mismatch", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    expect((await body()).paired).toBe(true);
  });

  it("an unstamped bundle reads as unknown, never as an empty string", async () => {
    vi.stubEnv("NEXT_PUBLIC_BUILD_ID", "");
    vi.stubGlobal("fetch", agentVersion({ sha: "x", api: TERMINAL_AGENT_API }));
    expect((await body()).terminal.build).toBe("unknown");
  });
});
