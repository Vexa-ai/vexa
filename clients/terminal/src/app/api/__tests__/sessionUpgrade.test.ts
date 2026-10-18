/** Hotfix 2026-10-18: a browser that signed in before this deployment could mint sessions still holds
 *  the old `terminal-login` KEY, and agent-api refuses its card press with `session_required` (D13).
 *  The terminal moves THAT browser onto a session in place — admin-api's `/internal/sessions/upgrade`
 *  with its own cookie key — swaps the cookie and retries once. It never offers anything but the
 *  browser's own cookie: no Company-mode tab, no delegation, and admin-api refuses a self-serve key. */
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

/** gateway + admin-api in one double: the gateway refuses any key but a session; admin-api upgrades
 *  only the tokens listed in `legacy`. */
function world(opts: { legacy?: Set<string>; upgradeStatus?: number } = {}) {
  const legacy = opts.legacy ?? new Set<string>();
  const sessions = new Set<string>();
  let n = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>).map(([k, v]) => [k.toLowerCase(), v]));
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, key: headers["x-api-key"], headers, body });
    if (url.endsWith("/internal/sessions/upgrade")) {
      if (opts.upgradeStatus) return new Response('{"detail":"no"}', { status: opts.upgradeStatus });
      const t = (body as { token: string }).token;
      if (!legacy.has(t)) return new Response('{"detail":{"refused":"not_a_login_key"}}', { status: 409 });
      legacy.delete(t);
      const s = `vxa_browser_sess${++n}`;
      sessions.add(s);
      return new Response(JSON.stringify({ token: s, id: 70 + n, user_id: 2, credential_class: "session" }), { status: 201 });
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
  it("is upgraded transparently: session minted for that key, cookie swapped, the press succeeds", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-1"]) });
    jar.set("vexa-token", "vxa_bot_old-login-1");
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(200);
    await expect(r.json()).resolves.toMatchObject({ pending_id: "p-1" });
    // the upgrade offered exactly the browser's own cookie key, with both secrets
    const [u] = upgrades();
    expect(u.body).toEqual({ token: "vxa_bot_old-login-1" });
    expect(u.headers["x-internal-secret"]).toBe("internal-secret");
    expect(u.headers["x-terminal-session-secret"]).toBe("session-secret");
    // first try with the key (refused), the retry with the session
    expect(asks().map((c) => c.key)).toEqual(["vxa_bot_old-login-1", "vxa_browser_sess1"]);
    const cookie = r.headers.get("set-cookie") || "";
    expect(cookie).toMatch(/^vexa-token=vxa_browser_sess1;/);
    expect(cookie).toMatch(/HttpOnly/);
    expect(cookie).toMatch(/SameSite=Lax/);
  });

  it("the answer route upgrades the same way", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-2"]) });
    jar.set("vexa-token", "vxa_bot_old-login-2");
    const r = await answerPOST(post("/api/asks/p-1/answer", { nonce: "n", content_hash: "h", reply: { label: "Yes" } }), { params: Promise.resolve({ id: "p-1" }) });
    expect(r.status).toBe(200);
    expect(r.headers.get("set-cookie")).toMatch(/^vexa-token=vxa_browser_sess1;/);
  });

  it("a page's parallel presses share one upgrade", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-3"]) });
    jar.set("vexa-token", "vxa_bot_old-login-3");
    const rs = await Promise.all([1, 2, 3].map(() => openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }))));
    expect(rs.map((r) => r.status)).toEqual([200, 200, 200]);
    expect(upgrades()).toHaveLength(1);
  });

  it("a session browser is never upgraded — nothing to do", async () => {
    // a session the double knows: mint one by upgrading first, then press with it
    world({ legacy: new Set(["vxa_bot_old-login-4"]) });
    jar.set("vexa-token", "vxa_bot_old-login-4");
    await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    calls = [];
    jar.set("vexa-token", "vxa_browser_sess1");
    expect((await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }))).status).toBe(200);
    expect(upgrades()).toHaveLength(0);
  });
});

describe("still refused: an API key or a delegated caller never gets a session", () => {
  it("a self-serve API key in the cookie — admin-api refuses the upgrade; the card is told to sign in again", async () => {
    world();   // nothing is a legacy login key
    jar.set("vexa-token", "vxa_bot_my-script-key");
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(403);
    await expect(r.json()).resolves.toMatchObject({ detail: { refused: "session_required", sign_in_again: true } });
    expect(r.headers.get("set-cookie")).toBeNull();
    expect(asks()).toHaveLength(1);   // no retry
  });

  it("a delegation token is never offered for an upgrade", async () => {
    world({ legacy: new Set(["vxd_worker-token"]) });
    jar.set("vexa-token", "vxd_worker-token");
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(403);
    expect(upgrades()).toHaveLength(0);
  });

  it("a Company-mode tab is never upgraded (the admin account's key; the person's waits aside)", async () => {
    world({ legacy: new Set(["vxa_bot_company-key"]) });
    jar.set("vexa-token", "vxa_bot_company-key");
    jar.set("vexa-person-token", "vxa_bot_person");
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(403);
    expect(upgrades()).toHaveLength(0);
  });

  it("a caller with no cookie at all (an API client hitting the BFF) is 401, nothing minted", async () => {
    world({ legacy: new Set(["vxa_bot_x"]) });
    const r = await openPOST(post("/api/asks/open", { session: "s-1", ask_id: "a" }));
    expect(r.status).toBe(401);
    expect(upgrades()).toHaveLength(0);
  });

  it("another origin is refused before anything is minted", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-5"]) });
    jar.set("vexa-token", "vxa_bot_old-login-5");
    const req = new Request("https://app.example.test/api/asks/open", { method: "POST", headers: { host: "app.example.test", origin: "https://evil.test" }, body: "{}" });
    expect((await openPOST(req)).status).toBe(403);
    expect(upgrades()).toHaveLength(0);
  });
});

describe("/api/auth/me upgrades an old-key browser on page load", () => {
  it("a key that is a legacy login key → a session cookie on the answer", async () => {
    world({ legacy: new Set(["vxa_bot_old-login-6"]) });
    jar.set("vexa-token", "vxa_bot_old-login-6");
    const r = await meGET();
    expect(r.status).toBe(200);
    expect(r.headers.get("set-cookie")).toMatch(/^vexa-token=vxa_browser_sess1;/);
  });

  it("a key admin-api will not upgrade leaves the tab exactly as signed in as it was", async () => {
    world();
    jar.set("vexa-token", "vxa_bot_my-script-key-2");
    const r = await meGET();
    expect(r.status).toBe(200);
    await expect(r.json()).resolves.toMatchObject({ authenticated: true });
    expect(r.headers.get("set-cookie")).toBeNull();
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
