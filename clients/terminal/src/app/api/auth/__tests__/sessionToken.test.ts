import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { findOrCreateUserToken, mintLoginToken, mintSessionToken } from "../adminApi";

/**
 * D13 (0.13.4): the sign-in mints a SESSION credential through admin-api's `POST /internal/sessions`
 * (internal tier + the terminal's own `X-Terminal-Session-Secret`), and falls back to the old
 * `bot,tx,browser` login key only when this terminal holds no session secret (hotfix 2026-10-18: with
 * the secret set, an admin-api answering 404/503 fails the sign-in rather than handing out a key). Synthetic fetch double; response shapes are admin-api's `app/sessions.py`.
 */

interface Call { method: string; path: string; headers: Record<string, string>; body: unknown }

function jsonRes(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let calls: Call[];
let sessionAnswer: () => Response;

function fakeFetch(url: string, init?: RequestInit): Promise<Response> {
  const u = new URL(url);
  const method = (init?.method || "GET").toUpperCase();
  const headers = Object.fromEntries(Object.entries((init?.headers ?? {}) as Record<string, string>)
    .map(([k, v]) => [k.toLowerCase(), v]));
  const body = init?.body ? JSON.parse(String(init.body)) : undefined;
  calls.push({ method, path: u.pathname, headers, body });
  if (u.pathname === "/internal/sessions" && method === "POST") return Promise.resolve(sessionAnswer());
  if (u.pathname === "/internal/signin-admission") return Promise.resolve(jsonRes({ admitted: true, why: "allow-list" }));
  if (u.pathname.startsWith("/admin/users/email/")) return Promise.resolve(jsonRes({ id: 42, email: "ada@example.com" }));
  const mint = u.pathname.match(/^\/admin\/users\/(\d+)\/tokens$/);
  if (mint && method === "POST") return Promise.resolve(jsonRes({ id: 9, token: "vxa_bot_key", user_id: 42, scopes: ["bot", "tx", "browser"] }, 201));
  if (mint && method === "GET") return Promise.resolve(jsonRes([]));
  return Promise.resolve(new Response("not found", { status: 404 }));
}

beforeEach(() => {
  calls = [];
  sessionAnswer = () => jsonRes({ token: "vxa_browser_sess", id: 7, user_id: 42, scopes: ["bot", "tx", "browser"], credential_class: "session" }, 201);
  vi.stubGlobal("fetch", vi.fn(fakeFetch));
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  vi.spyOn(console, "info").mockImplementation(() => undefined);
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "test-admin-key";
  process.env.VEXA_INTERNAL_API_SECRET = "test-internal-secret";
  process.env.VEXA_TERMINAL_SESSION_SECRET = "test-session-secret";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete process.env.VEXA_ADMIN_API_URL;
  delete process.env.VEXA_ADMIN_API_KEY;
  delete process.env.VEXA_INTERNAL_API_SECRET;
  delete process.env.VEXA_TERMINAL_SESSION_SECRET;
});

const sessionCalls = () => calls.filter((c) => c.path === "/internal/sessions");
const keyMints = () => calls.filter((c) => c.method === "POST" && /^\/admin\/users\/\d+\/tokens$/.test(c.path));

describe("mintSessionToken", () => {
  it("calls POST /internal/sessions with BOTH secrets and answers the session", async () => {
    const r = await mintSessionToken(42);
    expect(r.ok).toBe(true);
    expect(r.data).toMatchObject({ token: "vxa_browser_sess", id: 7, credential_class: "session" });
    const [c] = sessionCalls();
    expect(c.headers["x-internal-secret"]).toBe("test-internal-secret");
    expect(c.headers["x-terminal-session-secret"]).toBe("test-session-secret");
    expect(c.body).toEqual({ user_id: 42, name: "terminal-login" });
  });

  it("is unavailable, without calling admin-api, when the session secret is unset", async () => {
    delete process.env.VEXA_TERMINAL_SESSION_SECRET;
    const r = await mintSessionToken(42);
    expect(r).toMatchObject({ ok: false, unavailable: true });
    expect(sessionCalls()).toHaveLength(0);
  });

  it.each([404, 503])("is unavailable when admin-api answers %i", async (status) => {
    sessionAnswer = () => new Response("nope", { status });
    expect(await mintSessionToken(42)).toMatchObject({ ok: false, status, unavailable: true });
  });

  it("a 403 is a real refusal, not unavailability", async () => {
    sessionAnswer = () => new Response("Invalid terminal session secret", { status: 403 });
    const r = await mintSessionToken(42);
    expect(r.ok).toBe(false);
    expect(r.unavailable).toBe(false);
  });

  it("an answer that does not say session is not a session", async () => {
    sessionAnswer = () => jsonRes({ token: "vxa_bot_x", id: 1 }, 201);
    expect(await mintSessionToken(42)).toMatchObject({ ok: false, status: 502 });
  });
});

describe("mintLoginToken — the sign-in's credential", () => {
  it("is the session when one can be minted, and mints no key", async () => {
    const r = await mintLoginToken(42);
    expect(r).toMatchObject({ ok: true, data: { token: "vxa_browser_sess", id: 7 } });
    expect(keyMints()).toHaveLength(0);
  });

  it("falls back to the old login key, with a warning, only when this terminal holds no session secret", async () => {
    delete process.env.VEXA_TERMINAL_SESSION_SECRET;
    const r = await mintLoginToken(42);
    expect(r).toMatchObject({ ok: true, data: { token: "vxa_bot_key", id: 9 } });
    const [m] = keyMints();
    expect(m.path).toBe("/admin/users/42/tokens");
    expect(console.warn).toHaveBeenCalled();
  });

  // hotfix 2026-10-18: with the secret set, a browser is NEVER handed a key — that browser could not
  // press a card and would never learn why.
  it.each([404, 503])("with the secret set, an admin-api answering %i fails the sign-in instead of minting a key", async (status) => {
    sessionAnswer = () => new Response("", { status });
    const r = await mintLoginToken(42);
    expect(r.ok).toBe(false);
    expect(keyMints()).toHaveLength(0);
  });

  it("does not fall back on a real refusal", async () => {
    sessionAnswer = () => new Response("Invalid terminal session secret", { status: 403 });
    const r = await mintLoginToken(42);
    expect(r.ok).toBe(false);
    expect(keyMints()).toHaveLength(0);
  });
});

describe("findOrCreateUserToken signs in with the session", () => {
  it("mints a session even when the account already holds a live login key (never reuses the key)", async () => {
    // The founder's account on app.dev: live `terminal-login` keys, used minutes ago.
    const listed = [{ id: 469, user_id: 42, scopes: ["bot", "tx", "browser"], name: "terminal-login",
                      created_at: new Date(Date.now() - 3600_000).toISOString().replace(/Z$/, ""),
                      last_used_at: new Date().toISOString().replace(/Z$/, "") }];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      const u = new URL(url);
      if (u.pathname === "/admin/users/42/tokens" && (init?.method || "GET") === "GET") {
        calls.push({ method: "GET", path: u.pathname, headers: {}, body: undefined });
        return Promise.resolve(jsonRes(listed));
      }
      return fakeFetch(url, init);
    }));
    const r = await findOrCreateUserToken("ada@example.com");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.token).toBe("vxa_browser_sess");
    expect(sessionCalls()).toHaveLength(1);
    expect(keyMints()).toHaveLength(0);
  });

  it("returns the session token and still runs the login-token prune", async () => {
    const r = await findOrCreateUserToken("ada@example.com");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.token).toBe("vxa_browser_sess");
    expect(sessionCalls()).toHaveLength(1);
    expect(keyMints()).toHaveLength(0);
    expect(calls.some((c) => c.method === "GET" && c.path === "/admin/users/42/tokens")).toBe(true);
  });
});
