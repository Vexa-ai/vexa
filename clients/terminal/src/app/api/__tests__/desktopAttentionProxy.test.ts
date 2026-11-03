/** THE ATTENTION BFF (#1783, R3 `home2`, decision E1). `POST /api/desktop/attention` and
 *  `POST /api/desktop/refs` compose per request: crm-api's `desk/read` for the cards on the person's
 *  desk (crm is the only owner of cards; agent-api cannot read them), then agent-api's rule-based read
 *  model with those cards. Both with the person's OWN key; nothing stored. Without cards (crm off or
 *  refusing) the read model still answers — cards only add card items, they never gate the home. */
import { afterEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { POST as attention } from "../desktop/attention/route";
import { POST as refs } from "../desktop/refs/route";

const req = (path: string, body: unknown, headers: Record<string, string> = {}) =>
  new Request(`https://app.example.test${path}`, { method: "POST", headers: { host: "app.example.test", ...headers }, body: JSON.stringify(body) });

const CARD = {
  id: "k1", ref: "card:k1", type: "deal", revision: 3, title: "Renewal", about: null, state: "proposal", states: [], side_states: [],
  next: "Send terms", waiting: "u7", summary: "secret summary", due: null, progress: null, workspaces: ["alpha"], parent: null,
  children: { done: 0, total: 0, items: [] }, members: [{ subject: "u7", status: "member", name: "A" }], face: [{ type: "x" }],
  movement: null, stalled: false, can: { edit: true, transition: true, review: false, members: false }, href: "/c/k1",
};
const ANSWER = { generated_at: 1, needs: [], workspaces: {} };

afterEach(() => { jar = new Map(); vi.restoreAllMocks(); });

describe("POST /api/desktop/attention", () => {
  it("reads the desk's cards, then the read model with only the card fields the rules need", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ cards: [CARD] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(ANSWER), { status: 200 }));
    const r = await attention(req("/api/desktop/attention", { workspace: "alpha" }));
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual(ANSWER);
    const [u1] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [u2, i2] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(u1).toMatch(/\/crm\/desk\/read$/);
    expect(u2).toMatch(/\/agent\/desktop\/attention$/);
    expect((i2.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
    expect(JSON.parse(String(i2.body))).toEqual({
      workspace: "alpha",
      cards: [{ id: "k1", title: "Renewal", state: "proposal", next: "Send terms", waiting: "u7", due: null, workspaces: ["alpha"], members: [{ subject: "u7", status: "member" }], next_actor: null, open: true }],
    });
  });

  it("still answers when crm refuses or is down: no cards, never a failed home", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("{}", { status: 403 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(ANSWER), { status: 200 }));
    expect((await attention(req("/api/desktop/attention", {}))).status).toBe(200);
    expect(JSON.parse(String((fetchMock.mock.calls[1] as [string, RequestInit])[1].body))).toEqual({ cards: [] });
  });

  it("answers agent-api's refusal as given (home2 off)", async () => {
    jar.set("vexa-token", "person-key");
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ cards: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { reason: "feature_disabled" } }), { status: 403 }));
    expect((await attention(req("/api/desktop/attention", {}))).status).toBe(403);
  });

  it("refuses without a session, from another origin, and a non-string workspace", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    expect((await attention(req("/api/desktop/attention", {}))).status).toBe(401);
    jar.set("vexa-token", "person-key");
    expect((await attention(req("/api/desktop/attention", {}, { origin: "https://evil.test" }))).status).toBe(403);
    expect((await attention(req("/api/desktop/attention", { workspace: 5 }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /api/desktop/refs", () => {
  it("passes at most 100 refs, the page's workspace and the desk's cards", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ cards: [CARD] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ refs: {} }), { status: 200 }));
    const many = Array.from({ length: 150 }, (_, i) => `room:r${i}`);
    const r = await refs(req("/api/desktop/refs", { refs: ["card:k1", ...many, 7], workspace: "alpha" }));
    expect(r.status).toBe(200);
    const sent = JSON.parse(String((fetchMock.mock.calls[1] as [string, RequestInit])[1].body));
    expect(fetchMock.mock.calls[1][0]).toMatch(/\/agent\/desktop\/refs$/);
    expect(sent.refs).toHaveLength(100);
    expect(sent.workspace).toBe("alpha");
    expect(sent.cards[0].id).toBe("k1");
  });

  it("asks crm for cards only when a card ref is asked for", async () => {
    jar.set("vexa-token", "person-key");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({ refs: {} }), { status: 200 }));
    await refs(req("/api/desktop/refs", { refs: ["room:a"] }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/agent\/desktop\/refs$/);
  });
});
