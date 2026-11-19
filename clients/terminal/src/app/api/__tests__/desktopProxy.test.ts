/** THE DESKTOP'S BFF (#1783, desktop ADR D1). `GET /api/desktop` composes per request — crm-api's
 *  `desk/read` with the person's OWN key, then agent-api's `desktop/sections` with the placed refs —
 *  and stores nothing. A refusal from crm (feature off) is answered as given, so the page falls back
 *  to the current home. `POST /api/desktop/<arrange|proposal|undo>` forwards to `/crm/desk/<op>`. */
import { afterEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { GET } from "../desktop/route";
import { POST } from "../desktop/[op]/route";

const getReq = (headers: Record<string, string> = {}) =>
  new Request("https://app.example.test/api/desktop", { headers: { host: "app.example.test", ...headers } });
const postReq = (op: string, body: unknown, headers: Record<string, string> = {}) => [
  new Request(`https://app.example.test/api/desktop/${op}`, { method: "POST", headers: { host: "app.example.test", ...headers }, body: JSON.stringify(body) }),
  { params: Promise.resolve({ op }) },
] as const;

const DESK = { version: 4, undo_version: 3, saved: [], proposals: [], placements: [{ ref: "agent:morning" }, { ref: "file:alpha/a.md" }], layout: { sections: [] } };
const SECTIONS = { presence_visibility: "off", hidden_workspaces: 0, workspaces: [], agents: [], files: [] };

afterEach(() => { jar = new Map(); vi.restoreAllMocks(); });

describe("GET /api/desktop", () => {
  it("reads the desk then the sections with the person's own key, and answers the composed desktop", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(DESK), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(SECTIONS), { status: 200 }));
    const r = await GET(getReq());
    expect(r.status).toBe(200);
    const [u1, i1] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [u2, i2] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(u1).toMatch(/\/crm\/desk\/read$/);
    expect(u2).toMatch(/\/agent\/desktop\/sections$/);
    for (const init of [i1, i2]) {
      expect(init.method).toBe("POST");
      expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
    }
    expect(JSON.parse(String(i2.body))).toEqual({ refs: ["agent:morning", "file:alpha/a.md"] });
    const d = await r.json();
    expect(d.version).toBe(4);
    expect(d.sections.map((s: { id: string }) => s.id)).toEqual(["workspaces", "agents", "files", "cards"]);
    expect(r.headers.get("Cache-Control")).toContain("no-store");
  });

  it("answers crm's refusal as given (feature off) and never asks agent-api", async () => {
    jar.set("vexa-token", "person-key");
    const body = JSON.stringify({ status: "refused", reason: "feature_disabled", feature: "desktop" });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, { status: 403 }));
    const r = await GET(getReq());
    expect(r.status).toBe(403);
    expect(await r.json()).toMatchObject({ reason: "feature_disabled" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fetchMock.mockResolvedValue(new Response("{\"detail\":\"Not Found\"}", { status: 404 }));
    expect((await GET(getReq())).status).toBe(404);
  });

  it("refuses without a session and never falls back to a deployment key", async () => {
    process.env.VEXA_API_KEY = "deployment-key";
    const fetchMock = vi.spyOn(globalThis, "fetch");
    expect((await GET(getReq())).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    delete process.env.VEXA_API_KEY;
  });

  it("says the backend is unreachable rather than drawing an empty desktop", async () => {
    jar.set("vexa-token", "person-key");
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    expect((await GET(getReq())).status).toBe(503);
  });
});

describe("POST /api/desktop/<op>", () => {
  it("forwards arrange, proposal and undo to crm's desk routes with the person's key and passes the answer through", async () => {
    jar.set("vexa-token", "person-key");
    for (const op of ["arrange", "proposal", "undo"]) {
      const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{\"status\":\"refused\",\"reason\":\"desk_changed\",\"version\":9}", { status: 409 }));
      const r = await POST(...postReq(op, { ops: [{ op: "hide", section: "files" }], base_version: 4 }));
      expect(r.status).toBe(409);
      expect(await r.json()).toMatchObject({ reason: "desk_changed", version: 9 });
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toMatch(new RegExp(`/crm/desk/${op}$`));
      expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
      expect(JSON.parse(String(init.body))).toEqual({ ops: [{ op: "hide", section: "files" }], base_version: 4 });
      vi.restoreAllMocks();
    }
  });

  it("refuses an unknown verb, another origin, no session and a non-object body", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch");
    expect((await POST(...postReq("read", {}))).status).toBe(400);
    expect((await POST(...postReq("arrange/propose", {}))).status).toBe(400);
    expect((await POST(...postReq("arrange", {}, { origin: "https://evil.example" }))).status).toBe(403);
    expect((await POST(...postReq("arrange", [1, 2]))).status).toBe(400);
    jar = new Map();
    expect((await POST(...postReq("arrange", {}))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
