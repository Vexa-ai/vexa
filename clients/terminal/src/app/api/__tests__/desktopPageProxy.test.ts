/** THE PAGE BFF (PRD 0.13.4 D17/D20). `POST /api/desktop/page` reads the page's source, asks crm-api
 *  — with the person's own key — for each `{{crm …}}` block and each card block, then hands agent-api
 *  those answers keyed by the grammar's block key. crm down costs only its blocks. */
import { afterEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { POST as page } from "../desktop/page/route";

const req = (body: unknown, headers: Record<string, string> = {}) =>
  new Request("https://app.example.test/api/desktop/page", { method: "POST", headers: { host: "app.example.test", ...headers }, body: JSON.stringify(body) });

const README = `# T\n\n{{crm company="OeNB"}}\n\n{{commitments}}\n\n{{rooms}}\n`;

afterEach(() => { jar = new Map(); vi.restoreAllMocks(); });

function route(crmOk: boolean) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.includes("/agent/workspace/file")) return new Response(JSON.stringify({ path: "README.md", content: README }), { status: 200 });
    if (url.endsWith("/crm/desk/read")) return new Response(JSON.stringify({ cards: [] }), { status: 200 });
    if (url.endsWith("/crm/blocks/read")) return crmOk ? new Response(JSON.stringify({ view: "company", rows: [] }), { status: 200 }) : new Response("{}", { status: 503 });
    if (url.endsWith("/crm/card/list")) return crmOk ? new Response(JSON.stringify({ cards: [{ id: "c1", title: "Book venue", state: "open", summary: "SECRET" }] }), { status: 200 }) : new Response("{}", { status: 503 });
    if (url.endsWith("/agent/desktop/page")) return new Response(JSON.stringify({ v: 1, nodes: [] }), { status: 200 });
    return new Response("{}", { status: 404 });
  });
  return calls;
}

describe("POST /api/desktop/page", () => {
  it("refuses without a session or from another origin", async () => {
    expect((await page(req({}))).status).toBe(401);
    jar.set("vexa-token", "k");
    expect((await page(req({}, { origin: "https://evil.test" })))).toHaveProperty("status", 403);
  });

  it("asks crm per block with the person's key and passes the answers by block key", async () => {
    jar.set("vexa-token", "person-key");
    const calls = route(true);
    const r = await page(req({ workspace: "alpha" }));
    expect(r.status).toBe(200);
    const crm = calls.find((c) => c.url.endsWith("/crm/blocks/read"))!;
    expect((crm.init!.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
    expect(JSON.parse(String(crm.init!.body))).toEqual({ workspace: "alpha", view: "company", args: { company: "OeNB" } });
    const agent = calls.find((c) => c.url.endsWith("/agent/desktop/page"))!;
    const sent = JSON.parse(String(agent.init!.body));
    expect(Object.keys(sent.crm)).toEqual(['crm company="OeNB"']);
    expect(sent.card_blocks.commitments[0]).toMatchObject({ key: "c1", title: "Book venue", ref: "card:c1" });
    expect(JSON.stringify(sent)).not.toContain("SECRET");
  });

  it("crm down: the blocks are null (one quiet state), the page still renders", async () => {
    jar.set("vexa-token", "person-key");
    const calls = route(false);
    expect((await page(req({ workspace: "alpha" }))).status).toBe(200);
    const sent = JSON.parse(String(calls.find((c) => c.url.endsWith("/agent/desktop/page"))!.init!.body));
    expect(sent.crm['crm company="OeNB"']).toBeNull();
    expect(sent.card_blocks.commitments).toBeNull();
  });
});
