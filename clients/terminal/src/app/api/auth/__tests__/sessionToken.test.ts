import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { findOrCreateUserToken, mintLoginToken, mintSessionToken } from "../adminApi";

/**
 * D13 (0.13.4): the sign-in mints a SESSION credential through admin-api's `POST /internal/sessions`
 * (internal tier + the terminal's own `X-Terminal-Session-Secret`), and falls back to the old
 * `bot,tx,browser` login key only when the deployment cannot mint sessions yet (secret unset here,
 * admin-api 404 or 503). Synthetic fetch double; response shapes are admin-api's `app/sessions.py`.
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

  it.each([
    ["the secret is unset", () => { delete process.env.VEXA_TERMINAL_SESSION_SECRET; }],
    ["admin-api is older (404)", () => { sessionAnswer = () => new Response("", { status: 404 }); }],
    ["admin-api has no secret (503)", () => { sessionAnswer = () => new Response("", { status: 503 }); }],
  ])("falls back to the old login key, with a warning, when %s", async (_why, arrange) => {
    arrange();
    const r = await mintLoginToken(42);
    expect(r).toMatchObject({ ok: true, data: { token: "vxa_bot_key", id: 9 } });
    const [m] = keyMints();
    expect(m.path).toBe("/admin/users/42/tokens");
    expect(console.warn).toHaveBeenCalled();
  });

  it("does not fall back on a real refusal", async () => {
    sessionAnswer = () => new Response("Invalid terminal session secret", { status: 403 });
    const r = await mintLoginToken(42);
    expect(r.ok).toBe(false);
    expect(keyMints()).toHaveLength(0);
  });
});

describe("findOrCreateUserToken signs in with the session", () => {
  it("returns the session token and still runs the login-token prune", async () => {
    const r = await findOrCreateUserToken("ada@example.com");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.token).toBe("vxa_browser_sess");
    expect(sessionCalls()).toHaveLength(1);
    expect(keyMints()).toHaveLength(0);
    expect(calls.some((c) => c.method === "GET" && c.path === "/admin/users/42/tokens")).toBe(true);
  });
});
