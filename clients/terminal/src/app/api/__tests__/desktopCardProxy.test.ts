/** CARDS THROUGH THE DESKTOP'S BFF (#1783, desktop ADR D2). `POST /api/desktop/card/<op>` forwards the
 *  person's own card verbs to crm-api through the one edge with the signed-in person's OWN key —
 *  `read|create|update|members|membership|list` to `/crm/card/<op>`, `review` (Add / Not now on a card
 *  proposal) to `/crm/review` — and stores nothing: crm's status and body are answered as given. */
import { afterEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { POST } from "../desktop/card/[op]/route";

const req = (op: string, body: unknown, headers: Record<string, string> = {}) => [
  new Request(`https://app.example.test/api/desktop/card/${op}`, { method: "POST", headers: { host: "app.example.test", ...headers }, body: typeof body === "string" ? body : JSON.stringify(body) }),
  { params: Promise.resolve({ op }) },
] as const;

afterEach(() => { jar = new Map(); vi.restoreAllMocks(); });

describe("POST /api/desktop/card/<op>", () => {
  it("forwards each card verb to /crm/card/<op> with the person's key and relays status and body", async () => {
    jar.set("vexa-token", "person-key");
    for (const op of ["read", "create", "update", "members", "membership", "list"]) {
      const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(`{"status":"refused","reason":"revision_conflict","op":"${op}"}`, { status: 409 }));
      const r = await POST(...req(op, { card: "r1", expected_revision: 2 }));
      expect(r.status).toBe(409);
      expect(await r.json()).toEqual({ status: "refused", reason: "revision_conflict", op });
      expect(r.headers.get("Cache-Control")).toContain("no-store");
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toMatch(new RegExp(`/crm/card/${op}$`));
      expect(init.method).toBe("POST");
      expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
      expect(JSON.parse(String(init.body))).toEqual({ card: "r1", expected_revision: 2 });
      vi.restoreAllMocks();
    }
  });

  it("forwards review (Add / Not now on a card proposal) to /crm/review", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{\"status\":\"saved\"}", { status: 200 }));
    const r = await POST(...req("review", { proposal_id: "cp1", accept: true }));
    expect(r.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/crm\/review$/);
    expect(JSON.parse(String(init.body))).toEqual({ proposal_id: "cp1", accept: true });
  });

  it("answers crm's feature refusal as given", async () => {
    jar.set("vexa-token", "person-key");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{\"reason\":\"feature_disabled\",\"feature\":\"desktop_cards\"}", { status: 403 }));
    const r = await POST(...req("list", {}));
    expect(r.status).toBe(403);
    expect(await r.json()).toMatchObject({ feature: "desktop_cards" });
  });

  it("refuses an unknown verb, another origin, no session, bad JSON and a non-object body — never calling upstream", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch");
    expect((await POST(...req("propose", {}))).status).toBe(400);
    expect((await POST(...req("update/propose", {}))).status).toBe(400);
    expect((await POST(...req("delete", {}))).status).toBe(400);
    expect((await POST(...req("read", {}, { origin: "https://evil.example" }))).status).toBe(403);
    expect((await POST(...req("read", "{nope"))).status).toBe(400);
    expect((await POST(...req("read", [1]))).status).toBe(400);
    jar = new Map();
    process.env.VEXA_API_KEY = "deployment-key";
    expect((await POST(...req("read", {}))).status).toBe(401);
    delete process.env.VEXA_API_KEY;
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says crm is unreachable with 503", async () => {
    jar.set("vexa-token", "person-key");
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    expect((await POST(...req("read", { card: "r1" }))).status).toBe(503);
  });
});
