/** B1: a browser whose cookie still holds a pre-session `terminal-login` KEY is never traded up to a
 *  session in place. agent-api refuses its card press with `session_required` (D13); the terminal
 *  answers `sign_in_again`, and the person's next sign-in mints their session. Nothing — a press,
 *  a page load — asks admin-api to turn a key into a session: that door is gone. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

import { POST as openPOST } from "../asks/open/route";
import { POST as answerPOST } from "../asks/[id]/answer/route";
import { GET as meGET } from "../auth/me/route";
import { POST as tokensPOST } from "../tokens/route";

type Call = { url: string; key?: string; headers: Record<string, string>; body: unknown };
let calls: Call[];

const REFUSED = JSON.stringify({ detail: { refused: "session_required", note: "a card is answered from the signed-in app" } });

/** gateway + admin-api in one double: the gateway refuses any key but a session; admin-api would
 *  upgrade the tokens listed in `legacy` if anybody asked (nobody may). */
function world(opts: { legacy?: Set<string>; sessions?: Set<string> } = {}) {
  const legacy = opts.legacy ?? new Set<string>();
  const sessions = opts.sessions ?? new Set<string>();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>).map(([k, v]) => [k.toLowerCase(), v]));
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, key: headers["x-api-key"], headers, body });
    if (url.endsWith("/internal/sessions/upgrade")) {
      const t = (body as { token: string }).token;
      if (!legacy.has(t)) return new Response('{"detail":{"refused":"not_a_login_key"}}', { status: 409 });
      sessions.add("vxa_browser_upgraded");
      return new Response(JSON.stringify({ token: "vxa_browser_upgraded", id: 71, user_id: 2, credential_class: "session" }), { status: 201 });
    }
    if (url.endsWith("/internal/validate")) {
      const t = (body as { token: string }).token;
      return new Response(JSON.stringify({ user_id: 2, email: "dmitry@vexa.ai", credential_class: sessions.has(t) ? "session" : "key" }), { status: 200 });
    }
    if (url.includes("/agent/asks/")) {
      if (!sessions.has(headers["x-api-key"])) return new Response(REFUSED, { status: 403 });
      return new Response('{"pending_id":"p-1","nonce":"n","content_hash":"h","status":"open"}', { status: 200 });
    }
    return new Response("not found", { status: 404 });
  }));
}

const post = (path: string, body: unknown) => new Request(`https://app.example.test${path}`, {
  method: "POST", headers: { host: "app.example.test", origin: "https://app.example.test" }, body: JSON.stringify(body),
});
const upgrades = () => calls.filter((c) => c.url.endsWith("/internal/sessions/upgrade"));
const asks = () => calls.filter((c) => c.url.includes("/agent/asks/"));

beforeEach(() => {
  calls = [];
  jar = new Map();
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  process.env.VEXA_TERMINAL_SESSION_SECRET = "session-secret";
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  vi.spyOn(console, "info").mockImplementation(() => undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  for (const k of ["VEXA_ADMIN_API_URL", "VEXA_ADMIN_API_KEY", "VEXA_INTERNAL_API_SECRET", "VEXA_TERMINAL_SESSION_SECRET"]) delete process.env[k];
});

describe("a card press from a browser still holding its old login key", () => {
  it("is told to sign in again, and no key is ever traded for a session", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-1"]) });
    jar.set("vexa-token", "vxa_bot_old-login-1");
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(403);
    await expect(r.json()).resolves.toMatchObject({ detail: { refused: "session_required", sign_in_again: true } });
    expect(upgrades()).toHaveLength(0);
    expect(r.headers.get("set-cookie")).toBeNull();
    expect(asks()).toHaveLength(1);   // no retry with anything else
  });

  it("the answer route refuses the same way", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-2"]) });
    jar.set("vexa-token", "vxa_bot_old-login-2");
    const r = await answerPOST(post("/api/asks/p-1/answer", { nonce: "n", content_hash: "h", reply: { label: "Yes" } }), { params: Promise.resolve({ id: "p-1" }) });
    expect(r.status).toBe(403);
    expect(upgrades()).toHaveLength(0);
  });

  it("positive control: a browser holding a session presses through", async () => {
    world({ sessions: new Set(["vxa_browser_sess-ok"]) });
    jar.set("vexa-token", "vxa_browser_sess-ok");
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(200);
    await expect(r.json()).resolves.toMatchObject({ pending_id: "p-1" });
  });

  it("a caller with no cookie at all (an API client hitting the BFF) is 401", async () => {
    world();
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(401);
  });
});

describe("/api/auth/me never upgrades an old-key browser", () => {
  it("answers who it is and sets no cookie", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-6"]) });
    jar.set("vexa-token", "vxa_bot_old-login-6");
    const r = await meGET();
    expect(r.status).toBe(200);
    await expect(r.json()).resolves.toMatchObject({ authenticated: true });
    expect(r.headers.get("set-cookie")).toBeNull();
    expect(upgrades()).toHaveLength(0);
  });
});

describe("/api/tokens reserves the sign-in's names", () => {
  it.each(["terminal-login", "Terminal-Login", "company:2"])("refuses a self-serve key named %s", async (name) => {
    world();
    jar.set("vexa-token", "vxa_browser_sess-x");
    const req = new Request("https://app.example.test/api/tokens", { method: "POST", headers: { host: "app.example.test" }, body: JSON.stringify({ scopes: ["bot"], name }) });
    const r = await tokensPOST(req as never);
    expect(r.status).toBe(400);
    expect(calls.some((c) => /\/admin\/users\/\d+\/tokens/.test(c.url))).toBe(false);
  });
});
