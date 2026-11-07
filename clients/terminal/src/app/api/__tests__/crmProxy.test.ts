import { afterEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { POST } from "../crm/route";

const req = (body: unknown, headers: Record<string, string> = {}) =>
  new Request("https://app.example.test/api/crm", { method: "POST", headers: { host: "app.example.test", ...headers }, body: JSON.stringify(body) });

afterEach(() => { jar = new Map(); vi.restoreAllMocks(); });

describe("the CRM record page's proxy (ADR-0047)", () => {
  it("relays the edge's 404 where the deployment runs no CRM", async () => {
    jar.set("vexa-token", "person-key");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{\"detail\":\"Not Found\"}", { status: 404 }));
    expect((await POST(req({ operation: "read", record_id: "r" }))).status).toBe(404);
  });
  it("forwards the signed-in person's own credential to the gateway, and only read, history and review", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{\"id\":\"r\"}", { status: 200 }));
    const ok = await POST(req({ operation: "read", record_id: "r" }));
    expect(ok.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/crm\/read$/);
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
    expect(JSON.parse(String(init.body))).toEqual({ record_id: "r" });
    // R4: the contact's public-profile link (read live, link / unlink)
    const pr = await POST(req({ operation: "profile/read", record_id: "r" }));
    expect(pr.status).toBe(200);
    expect(fetchMock.mock.calls.at(-1)![0]).toMatch(/\/crm\/profile\/read$/);
    for (const operation of ["change", "bind", "configure", "describe"]) {
      expect((await POST(req({ operation }))).status).toBe(400);
    }
  });
  it("refuses without a session and from another origin", async () => {
    expect((await POST(req({ operation: "read" }))).status).toBe(401);
    jar.set("vexa-token", "person-key");
    expect((await POST(req({ operation: "read" }, { origin: "https://evil.example" }))).status).toBe(403);
  });
});
