/** 0.13.4 S5: the card core's verbs (`inbox`, `seen`, `nonce`, `decide`) and the 7g ask routes travel
 *  with the signed-in person's OWN key and store nothing; no key → 401, never a deployment key. */
import { afterEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { POST as cardPOST } from "../desktop/card/[op]/route";
import { POST as openPOST } from "../asks/open/route";
import { POST as answerPOST } from "../asks/[id]/answer/route";

const post = (path: string, body: unknown) => new Request(`https://app.example.test${path}`, { method: "POST", headers: { host: "app.example.test" }, body: JSON.stringify(body) });

afterEach(() => { jar = new Map(); vi.restoreAllMocks(); });

describe("card core through the desktop BFF", () => {
  it("forwards inbox / seen / nonce / decide to /crm/card/<op> with the person's key", async () => {
    jar.set("vexa-token", "person-key");
    for (const op of ["inbox", "seen", "nonce", "decide"]) {
      const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(`{"op":"${op}"}`, { status: op === "decide" ? 409 : 200 }));
      const r = await cardPOST(post(`/api/desktop/card/${op}`, { card: "c-1" }), { params: Promise.resolve({ op }) });
      expect(r.status).toBe(op === "decide" ? 409 : 200);
      const [url, init] = f.mock.calls[0] as [string, RequestInit];
      expect(url).toMatch(new RegExp(`/crm/card/${op}$`));
      expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
      vi.restoreAllMocks();
    }
  });
});

describe("7g ask routes", () => {
  it("open and answer go to agent-api with the person's key and relay its answer", async () => {
    jar.set("vexa-token", "person-key");
    let f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{"pending_id":"p-1","nonce":"n","content_hash":"h","status":"open"}', { status: 200 }));
    let r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(200);
    expect(String(f.mock.calls[0][0])).toMatch(/\/agent\/asks\/open$/);
    expect(((f.mock.calls[0][1] as RequestInit).headers as Record<string, string>)["X-API-Key"]).toBe("person-key");
    vi.restoreAllMocks();
    f = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{"detail":"replayed"}', { status: 409 }));
    r = await answerPOST(post("/api/asks/p-1/answer", { nonce: "n", content_hash: "h", reply: { label: "Yes" } }), { params: Promise.resolve({ id: "p-1" }) });
    expect(r.status).toBe(409);
    expect(String(f.mock.calls[0][0])).toMatch(/\/agent\/asks\/p-1\/answer$/);
  });

  it("refuses without a signed-in person, and a malformed id", async () => {
    const f = vi.spyOn(globalThis, "fetch");
    expect((await openPOST(post("/api/asks/open", { session: "s", ask_id: "a" }))).status).toBe(401);
    jar.set("vexa-token", "person-key");
    expect((await answerPOST(post("/api/asks/x/answer", {}), { params: Promise.resolve({ id: "../etc" }) })).status).toBe(400);
    expect(f).not.toHaveBeenCalled();
  });
});
