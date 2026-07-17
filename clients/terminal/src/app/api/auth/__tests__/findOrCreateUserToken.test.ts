import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { findOrCreateUserToken } from "../adminApi";

/**
 * D-A2 fixture — a deterministic in-memory fake admin-api behind `fetch`.
 *
 * (a) deterministic producer: holds a `userId -> tokens[]` map, serves GET /tokens from it,
 *     appends on POST .../tokens, removes on DELETE /tokens/{id}, and records every call.
 * (b) captured live output: response shapes are the real admin-api ones quoted from
 *     core/identity/services/admin-api/src/admin_api/app/main.py —
 *     TokenResponse {id, token, user_id, scopes, name} on mint, TokenInfo[] (NO `token`) on list,
 *     204 on delete.
 * (c) hand-authored edges are the individual `it()` cases below.
 */

interface FakeTokenInfo {
  id: number;
  user_id: number;
  scopes: string[];
  name: string | null;
  created_at: string;
}

interface Recorded {
  method: string;
  path: string;
}

function jsonRes(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

class FakeAdminApi {
  users = new Map<string, { id: number; email: string }>();
  tokens = new Map<number, FakeTokenInfo[]>();
  calls: Recorded[] = [];
  private nextUserId = 100;
  private nextTokenId = 1000;
  private clock = 0;
  // when set to a token id, the first DELETE of that id 404s (concurrent-delete edge)
  revoke404For: number | null = null;

  seedUser(email: string): { id: number; email: string } {
    const u = { id: this.nextUserId++, email };
    this.users.set(email.toLowerCase(), u);
    this.tokens.set(u.id, []);
    return u;
  }

  seedToken(userId: number, name: string | null): FakeTokenInfo {
    const tok: FakeTokenInfo = {
      id: this.nextTokenId++,
      user_id: userId,
      scopes: ["bot", "tx", "browser"],
      name,
      created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, this.clock++)).toISOString(),
    };
    this.tokens.get(userId)!.push(tok);
    return tok;
  }

  liveTokens(userId: number): FakeTokenInfo[] {
    return this.tokens.get(userId) ?? [];
  }

  countRevokes(): number {
    return this.calls.filter((c) => c.method === "DELETE").length;
  }

  fetch = async (url: string, init?: RequestInit): Promise<Response> => {
    const u = new URL(url);
    const method = (init?.method || "GET").toUpperCase();
    const path = u.pathname;
    this.calls.push({ method, path });

    // GET /admin/users/email/{email}
    let m = path.match(/^\/admin\/users\/email\/(.+)$/);
    if (m && method === "GET") {
      const email = decodeURIComponent(m[1]).toLowerCase();
      const user = this.users.get(email);
      return user ? jsonRes(user) : new Response("", { status: 404 });
    }

    // POST /admin/users
    if (path === "/admin/users" && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const user = this.seedUser(body.email);
      return jsonRes(user);
    }

    // POST /admin/users/{id}/tokens  (mint)
    m = path.match(/^\/admin\/users\/([^/]+)\/tokens$/);
    if (m && method === "POST") {
      const userId = Number(m[1]);
      const name = u.searchParams.get("name");
      const scopes = (u.searchParams.get("scopes") || "").split(",").filter(Boolean);
      const tok = this.seedToken(userId, name);
      tok.scopes = scopes.length ? scopes : tok.scopes;
      // TokenResponse — the ONLY place the secret crosses
      return jsonRes({ ...tok, token: `secret-${tok.id}` });
    }

    // GET /admin/users/{id}/tokens  (list — metadata only, NEVER the secret)
    if (m && method === "GET") {
      const userId = Number(m[1]);
      const list = this.liveTokens(userId).map(({ id, user_id, scopes, name, created_at }) => ({
        id,
        user_id,
        scopes,
        name,
        created_at,
      }));
      return jsonRes(list);
    }

    // DELETE /admin/tokens/{id}
    m = path.match(/^\/admin\/tokens\/([^/]+)$/);
    if (m && method === "DELETE") {
      const tokenId = Number(m[1]);
      if (this.revoke404For === tokenId) {
        this.revoke404For = null;
        return new Response("", { status: 404 });
      }
      for (const [uid, list] of this.tokens) {
        const idx = list.findIndex((t) => t.id === tokenId);
        if (idx >= 0) {
          list.splice(idx, 1);
          this.tokens.set(uid, list);
          break;
        }
      }
      return new Response("", { status: 204 });
    }

    return new Response("not found", { status: 404 });
  };
}

let fake: FakeAdminApi;

beforeEach(() => {
  fake = new FakeAdminApi();
  vi.stubGlobal("fetch", vi.fn(fake.fetch));
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "test-admin-key";
  // allowlist configured → the bootstrap-admin internal call short-circuits (no /internal hit)
  process.env.VEXA_ADMIN_EMAILS = "owner@vexa.ai";
  process.env.VEXA_TERMINAL_LOGIN_TOKEN_CAP = "3";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.VEXA_ADMIN_API_URL;
  delete process.env.VEXA_ADMIN_API_KEY;
  delete process.env.VEXA_ADMIN_EMAILS;
  delete process.env.VEXA_TERMINAL_LOGIN_TOKEN_CAP;
});

describe("findOrCreateUserToken — bounded login-token minting (#638)", () => {
  it("A1: K=6 sign-ins for one user stay bounded at cap N=3 (oldest login tokens pruned)", async () => {
    const user = fake.seedUser("loop@vexa.ai");

    for (let i = 0; i < 6; i++) {
      const r = await findOrCreateUserToken("loop@vexa.ai");
      expect(r.ok).toBe(true);
    }

    const live = fake.liveTokens(user.id);
    // THE VALUE: final live-token count is bounded to the cap, not one-per-sign-in.
    expect(live.length).toBe(3);
    // all survivors are the login-named tokens (nothing else existed to prune)
    expect(live.every((t) => t.name === "terminal-login")).toBe(true);

    // recorder shows exactly 3 revokes (calls 4,5,6 each prune one overflow token)
    expect(fake.countRevokes()).toBe(3);
    // the survivors are the 3 NEWEST login tokens (oldest pruned first)
    const surviving = live.map((t) => t.id).sort((a, b) => a - b);
    expect(surviving).toEqual([1003, 1004, 1005]);
  });

  it("A2: N-1 login tokens + one self-serve token → mint once, revoke 0, self-serve untouched", async () => {
    const user = fake.seedUser("dev@vexa.ai");
    fake.seedToken(user.id, "terminal-login"); // id 1000
    fake.seedToken(user.id, "terminal-login"); // id 1001
    const selfServe = fake.seedToken(user.id, "my-ci-key"); // id 1002

    const r = await findOrCreateUserToken("dev@vexa.ai");
    expect(r.ok).toBe(true);

    // exactly one mint this sign-in
    const mintCalls = fake.calls.filter((c) => c.method === "POST" && /\/tokens$/.test(c.path));
    expect(mintCalls.length).toBe(1);

    // 2 existing login + 1 new = 3 = cap → nothing to prune
    expect(fake.countRevokes()).toBe(0);

    const live = fake.liveTokens(user.id);
    // self-serve token still present and untouched
    const stillThere = live.find((t) => t.id === selfServe.id);
    expect(stillThere).toBeDefined();
    expect(stillThere!.name).toBe("my-ci-key");
    // 3 login tokens + 1 self-serve = 4 live
    expect(live.length).toBe(4);
    expect(live.filter((t) => t.name === "terminal-login").length).toBe(3);
  });

  it("A2b: a self-serve token is NEVER pruned even when login tokens are well over cap", async () => {
    const user = fake.seedUser("busy@vexa.ai");
    for (let i = 0; i < 5; i++) fake.seedToken(user.id, "terminal-login"); // ids 1000..1004
    const selfServe = fake.seedToken(user.id, "my-ci-key"); // id 1005

    const r = await findOrCreateUserToken("busy@vexa.ai");
    expect(r.ok).toBe(true);

    const live = fake.liveTokens(user.id);
    // 5 existing + 1 new = 6 login tokens, cap 3 → 3 pruned
    expect(fake.countRevokes()).toBe(3);
    expect(live.filter((t) => t.name === "terminal-login").length).toBe(3);
    // self-serve survives untouched regardless of overflow
    expect(live.find((t) => t.id === selfServe.id)?.name).toBe("my-ci-key");
  });

  it("edge: a 404 mid-prune is swallowed and the sign-in still succeeds", async () => {
    const user = fake.seedUser("racy@vexa.ai");
    for (let i = 0; i < 4; i++) fake.seedToken(user.id, "terminal-login"); // ids 1000..1003
    // the oldest overflow token 404s when revoked (deleted concurrently)
    fake.revoke404For = 1000;

    const r = await findOrCreateUserToken("racy@vexa.ai");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.token).toMatch(/^secret-/);

    // 5 login tokens, cap 3 → 2 overflow revoke attempts; one 404s but is swallowed
    expect(fake.countRevokes()).toBe(2);
  });

  it("edge: a fresh user with zero tokens mints exactly once and prunes nothing", async () => {
    const r = await findOrCreateUserToken("new@vexa.ai");
    expect(r.ok).toBe(true);
    expect(fake.countRevokes()).toBe(0);

    const created = fake.users.get("new@vexa.ai");
    expect(created).toBeDefined();
    expect(fake.liveTokens(created!.id).length).toBe(1);
  });

  it("mints with the terminal-login name and bot,tx,browser scopes", async () => {
    await findOrCreateUserToken("scoped@vexa.ai");
    const created = fake.users.get("scoped@vexa.ai")!;
    const tok = fake.liveTokens(created.id)[0];
    expect(tok.name).toBe("terminal-login");
    expect(tok.scopes).toEqual(["bot", "tx", "browser"]);
  });
});
