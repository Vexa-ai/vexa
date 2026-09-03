import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { findOrCreateUserToken, parseAdminTimestamp } from "../adminApi";

/**
 * D-A2 fixture — a deterministic in-memory fake admin-api behind `fetch`.
 *
 * (a) deterministic producer: holds a `userId -> tokens[]` map, serves GET /tokens from it,
 *     appends on POST .../tokens, removes on DELETE /tokens/{id}, and records every call.
 * (b) captured live output: response shapes are the real admin-api ones quoted from
 *     core/identity/services/admin-api/src/admin_api/app/main.py —
 *     TokenResponse {id, token, user_id, scopes, name} on mint, TokenInfo[] (NO `token`) on list,
 *     204 on delete. Timestamps are serialised the way admin-api really serialises them:
 *     NAIVE UTC with no zone designator (`datetime.utcnow()` through Pydantic), because the prune
 *     has to read them as UTC rather than as local time — see `parseAdminTimestamp`.
 * (c) hand-authored edges are the individual `it()` cases below.
 *
 * WHAT CHANGED HERE, AND WHY (2026-09-01). The fixture used to model only `created_at`, because the
 * prune only looked at `created_at` — it revoked the OLDEST-ISSUED tokens over the cap. That policy
 * made the longest-lived session the first casualty of everybody else's sign-ins, and it really did
 * evict the founder's browser session repeatedly while agents redeemed magic links against the same
 * deploy. The prune now ranks by LAST USE, so the fixture must model `last_used_at` — and the tests
 * below have to distinguish the two orderings rather than let them coincide, which is why several of
 * them deliberately give a token an issue order that DISAGREES with its use order.
 */

const HOUR_MS = 3600_000;

interface FakeTokenInfo {
  id: number;
  user_id: number;
  scopes: string[];
  name: string | null;
  created_at: string;
  last_used_at: string | null;
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
  /** Real wall-clock at construction — the prune compares against a real `Date.now()`, so every
   *  fixture age is expressed relative to this rather than to a fabricated epoch. */
  readonly base = Date.now();
  /** Distinct events happen at distinct instants. Without this every stamp in a test tied exactly,
   *  the sort fell through to its id tiebreak, and "lowest id first" quietly reproduced the very
   *  issue-order eviction this fixture exists to catch — B1 failed for a fixture reason that looked
   *  exactly like the bug. One millisecond per recorded event keeps call order visible while
   *  staying far below the hour-scale ages the tests express. */
  private tick = 0;
  // when set to a token id, the first DELETE of that id 404s (concurrent-delete edge)
  revoke404For: number | null = null;

  /** admin-api's wire format: ISO, microsecond-ish, and NO trailing `Z`. */
  private stamp(hoursAgo: number): string {
    return new Date(this.base - hoursAgo * HOUR_MS + this.tick++).toISOString().replace(/Z$/, "");
  }

  seedUser(email: string): { id: number; email: string } {
    const u = { id: this.nextUserId++, email };
    this.users.set(email.toLowerCase(), u);
    this.tokens.set(u.id, []);
    return u;
  }

  /** Seed a token with an explicit history. `createdHoursAgo` defaults to 0 (just issued) and
   *  `usedHoursAgo` to null (never authenticated anything) — which is exactly what the MINT path
   *  produces, so the same helper serves both. */
  seedToken(
    userId: number,
    name: string | null,
    opts: { createdHoursAgo?: number; usedHoursAgo?: number | null } = {},
  ): FakeTokenInfo {
    const createdHoursAgo = opts.createdHoursAgo ?? 0;
    const usedHoursAgo = opts.usedHoursAgo ?? null;
    const tok: FakeTokenInfo = {
      id: this.nextTokenId++,
      user_id: userId,
      scopes: ["bot", "tx", "browser"],
      name,
      created_at: this.stamp(createdHoursAgo),
      last_used_at: usedHoursAgo === null ? null : this.stamp(usedHoursAgo),
    };
    this.tokens.get(userId)!.push(tok);
    return tok;
  }

  /** Stamp a token as having just authenticated a request — what a live browser tab does to its own
   *  token every few seconds by simply being open. */
  useToken(tokenId: number, hoursAgo = 0): void {
    for (const list of this.tokens.values()) {
      const t = list.find((x) => x.id === tokenId);
      if (t) { t.last_used_at = this.stamp(hoursAgo); return; }
    }
  }

  liveTokens(userId: number): FakeTokenInfo[] {
    return this.tokens.get(userId) ?? [];
  }

  liveIds(userId: number): number[] {
    return this.liveTokens(userId).map((t) => t.id).sort((a, b) => a - b);
  }

  alive(tokenId: number): boolean {
    for (const list of this.tokens.values()) if (list.some((t) => t.id === tokenId)) return true;
    return false;
  }

  countRevokes(): number {
    return this.calls.filter((c) => c.method === "DELETE").length;
  }

  revokedIds(): number[] {
    return this.calls
      .filter((c) => c.method === "DELETE")
      .map((c) => Number(c.path.split("/").pop()));
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

    // POST /admin/users/{id}/tokens  (mint — issued now, never used)
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
      const list = this.liveTokens(userId).map(
        ({ id, user_id, scopes, name, created_at, last_used_at }) => ({
          id, user_id, scopes, name, created_at, last_used_at,
        }),
      );
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
      // 204 means NO body — `new Response("", {status: 204})` throws, which made every revoke in
      // this fixture report a failure the prune then swallowed, hiding whether the 404 edge below
      // was actually the thing being exercised.
      return new Response(null, { status: 204 });
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
  delete process.env.VEXA_TERMINAL_LOGIN_TOKEN_MAX;
  delete process.env.VEXA_TERMINAL_LOGIN_RECENT_USE_HOURS;
});

describe("parseAdminTimestamp — admin-api serialises naive UTC", () => {
  it("reads a zone-less datetime as UTC, not as local time", () => {
    // The exact shape admin-api emits. Read as local time this is off by the host's offset, which
    // silently breaks the absolute recent-use window on any non-UTC machine.
    expect(parseAdminTimestamp("2026-09-01T16:33:46.315228")).toBe(Date.parse("2026-09-01T16:33:46.315Z"));
  });

  it("leaves an already-zoned datetime alone", () => {
    expect(parseAdminTimestamp("2026-09-01T16:33:46.315Z")).toBe(Date.parse("2026-09-01T16:33:46.315Z"));
    expect(parseAdminTimestamp("2026-09-01T18:33:46.315+02:00")).toBe(Date.parse("2026-09-01T16:33:46.315Z"));
  });

  it("returns NaN for absent or unparseable input", () => {
    expect(parseAdminTimestamp(null)).toBeNaN();
    expect(parseAdminTimestamp(undefined)).toBeNaN();
    expect(parseAdminTimestamp("   ")).toBeNaN();
    expect(parseAdminTimestamp("not a date")).toBeNaN();
  });
});

describe("findOrCreateUserToken — login tokens are pruned by LAST USE (#638, regressed 2026-09-01)", () => {
  it("B1: the live session survives an arbitrary number of other sign-ins — THE REGRESSION", async () => {
    // The founder's case, exactly. His token is the OLDEST-ISSUED thing the account owns; it is also
    // the one being used right now, because his tab is open and making requests. Under the old
    // created_at policy it was the prune victim EVERY time somebody else signed in. Here the ceiling
    // is pinned to the cap so the prune is under maximum pressure and has no slack to be generous
    // with — the survival below is bought by the ranking alone, not by the recent-use exemption.
    process.env.VEXA_TERMINAL_LOGIN_TOKEN_MAX = "3";
    const user = fake.seedUser("founder@vexa.ai");
    const live = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 2000, usedHoursAgo: 0 });

    for (let i = 0; i < 6; i++) {
      const r = await findOrCreateUserToken("founder@vexa.ai");
      expect(r.ok).toBe(true);
      // his tab keeps working while the agents sign in — every few seconds it authenticates again
      fake.useToken(live.id, 0);
      expect(fake.alive(live.id)).toBe(true);
    }

    expect(fake.alive(live.id)).toBe(true);
    expect(fake.revokedIds()).not.toContain(live.id);
    // and the cap really is being enforced around him — this is not survival by nothing happening
    expect(fake.countRevokes()).toBeGreaterThan(0);
    expect(fake.liveTokens(user.id).length).toBe(3);
  });

  it("B2: a session idle for hours is spared as over-cap rather than revoked", async () => {
    // The same tab, but the founder stepped away. Ranking alone would now put him at the
    // least-recently-used end, because six tokens have been minted since. The recent-use window is
    // what saves him: he authenticated 3 hours ago, well inside the 48h default.
    const user = fake.seedUser("away@vexa.ai");
    const idle = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 2000, usedHoursAgo: 3 });

    for (let i = 0; i < 6; i++) expect((await findOrCreateUserToken("away@vexa.ai")).ok).toBe(true);

    expect(fake.alive(idle.id)).toBe(true);
    expect(fake.revokedIds()).not.toContain(idle.id);
  });

  it("B3: once that session goes quiet past the window it is the FIRST to go", async () => {
    // The other half of B2 — the exemption is a window, not an amnesty. Same token, last used five
    // days ago, three fresh tokens alongside it: now it is genuinely the least-recently-used and the
    // cap reclaims it.
    const user = fake.seedUser("gone@vexa.ai");
    const stale = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 2000, usedHoursAgo: 120 });
    fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 100, usedHoursAgo: 1 });
    fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 100, usedHoursAgo: 2 });

    expect((await findOrCreateUserToken("gone@vexa.ai")).ok).toBe(true);

    expect(fake.alive(stale.id)).toBe(false);
    expect(fake.revokedIds()).toEqual([stale.id]);
  });

  it("C: the ranking is USE order, not ISSUE order, when the two disagree", async () => {
    // Deliberately opposed: the NEWEST-issued token is the one nobody has touched in a week, and the
    // OLDEST-issued one was used an hour ago. The old policy would have revoked the oldest-issued;
    // the correct victim is the least-recently-used.
    const user = fake.seedUser("mixed@vexa.ai");
    const oldIssuedFreshlyUsed = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 500, usedHoursAgo: 1 });
    const midIssuedStale = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 300, usedHoursAgo: 200 });
    const newIssuedStalest = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 10, usedHoursAgo: 400 });

    // 3 existing + 1 minted = 4, cap 3 → exactly one candidate: the least-recently-USED.
    expect((await findOrCreateUserToken("mixed@vexa.ai")).ok).toBe(true);

    expect(fake.revokedIds()).toEqual([newIssuedStalest.id]);
    expect(fake.alive(oldIssuedFreshlyUsed.id)).toBe(true);
    expect(fake.alive(midIssuedStale.id)).toBe(true);
  });

  it("A1: a fleet of quiet login tokens converges to the cap on the next sign-in", async () => {
    // What A1 used to assert — "K sign-ins stay bounded at N" — is still the steady state, but it is
    // now a statement about QUIET tokens rather than about issue order. Six tokens nobody has used
    // for days, one sign-in, and the account is back at the cap; the survivors are the two
    // most-recently-used plus the one just minted.
    const user = fake.seedUser("loop@vexa.ai");
    const ages = [200, 190, 180, 170, 160, 150]; // hours since last use, oldest first
    const seeded = ages.map((h) => fake.seedToken(user.id, "terminal-login", { createdHoursAgo: h + 1, usedHoursAgo: h }));

    expect((await findOrCreateUserToken("loop@vexa.ai")).ok).toBe(true);

    const live = fake.liveTokens(user.id);
    expect(live.length).toBe(3);
    expect(live.every((t) => t.name === "terminal-login")).toBe(true);
    // the four least-recently-used are gone; the two most-recently-used survive alongside the mint
    expect(fake.revokedIds()).toEqual([seeded[0].id, seeded[1].id, seeded[2].id, seeded[3].id]);
    expect(fake.alive(seeded[4].id)).toBe(true);
    expect(fake.alive(seeded[5].id)).toBe(true);
  });

  it("A1b: a same-day BURST is bounded by the ceiling, not by the cap — the deliberate trade", async () => {
    // Every token minted in a burst is recent, so the recent-use exemption spares all of them and the
    // cap alone would hold nothing back. `VEXA_TERMINAL_LOGIN_TOKEN_MAX` is what keeps that bounded.
    // The set is over the cap on purpose and drains by itself once these tokens go quiet (A1).
    process.env.VEXA_TERMINAL_LOGIN_TOKEN_MAX = "5";
    const user = fake.seedUser("burst@vexa.ai");

    for (let i = 0; i < 8; i++) expect((await findOrCreateUserToken("burst@vexa.ai")).ok).toBe(true);

    const live = fake.liveTokens(user.id);
    expect(live.length).toBe(5);              // the ceiling, above the cap of 3
    expect(live.length).toBeGreaterThan(Number(process.env.VEXA_TERMINAL_LOGIN_TOKEN_CAP));
    // and what went was the least-recently-active end, in order
    expect(fake.revokedIds()).toEqual([1000, 1001, 1002]);
  });

  it("A2: N-1 login tokens + one self-serve token → mint once, revoke 0, self-serve untouched", async () => {
    const user = fake.seedUser("dev@vexa.ai");
    fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 500, usedHoursAgo: 400 }); // id 1000
    fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 400, usedHoursAgo: 300 }); // id 1001
    const selfServe = fake.seedToken(user.id, "my-ci-key", { createdHoursAgo: 300, usedHoursAgo: 200 }); // id 1002

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
    // five long-quiet login tokens (ids 1000..1004) — all genuinely prunable
    for (let i = 0; i < 5; i++) {
      fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 500 - i, usedHoursAgo: 400 - i });
    }
    const selfServe = fake.seedToken(user.id, "my-ci-key", { createdHoursAgo: 900, usedHoursAgo: 900 }); // id 1005

    const r = await findOrCreateUserToken("busy@vexa.ai");
    expect(r.ok).toBe(true);

    const live = fake.liveTokens(user.id);
    // 5 existing + 1 new = 6 login tokens, cap 3 → 3 pruned
    expect(fake.countRevokes()).toBe(3);
    expect(live.filter((t) => t.name === "terminal-login").length).toBe(3);
    // the self-serve key is the single least-recently-used token the account owns, and is STILL
    // untouched — the prune never considers anything outside the login-named set.
    expect(live.find((t) => t.id === selfServe.id)?.name).toBe("my-ci-key");
    expect(fake.revokedIds()).not.toContain(selfServe.id);
  });

  it("edge: a token that has NEVER authenticated anything is ranked at its issue time", async () => {
    // `last_used_at` is null until the token's first validated request. Falling back to `created_at`
    // is what stops a just-minted token from being ranked as infinitely stale and pruning itself.
    const user = fake.seedUser("unused@vexa.ai");
    const ancientUnused = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 900, usedHoursAgo: null });
    const recentUnused = fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 300, usedHoursAgo: null });
    fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 200, usedHoursAgo: 100 });

    expect((await findOrCreateUserToken("unused@vexa.ai")).ok).toBe(true);

    // 4 login tokens, cap 3 → one candidate, and it is the one issued longest ago among the unused
    expect(fake.revokedIds()).toEqual([ancientUnused.id]);
    expect(fake.alive(recentUnused.id)).toBe(true);
  });

  it("edge: a 404 mid-prune is swallowed and the sign-in still succeeds", async () => {
    const user = fake.seedUser("racy@vexa.ai");
    // ids 1000..1003, all long quiet so all four are genuine prune candidates
    for (let i = 0; i < 4; i++) {
      fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 500 - i, usedHoursAgo: 400 - i });
    }
    // the least-recently-used overflow token 404s when revoked (deleted concurrently)
    fake.revoke404For = 1000;

    const r = await findOrCreateUserToken("racy@vexa.ai");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.token).toMatch(/^secret-/);

    // 5 login tokens, cap 3 → 2 overflow revoke attempts; one 404s but is swallowed
    expect(fake.countRevokes()).toBe(2);
  });

  it("edge: a list failure skips the prune entirely and the sign-in still succeeds", async () => {
    const user = fake.seedUser("blind@vexa.ai");
    for (let i = 0; i < 5; i++) {
      fake.seedToken(user.id, "terminal-login", { createdHoursAgo: 500 - i, usedHoursAgo: 400 - i });
    }
    const real = fake.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      const isList = (init?.method || "GET").toUpperCase() === "GET" && /\/tokens$/.test(new URL(url).pathname);
      return isList ? new Response("upstream exploded", { status: 500 }) : real(url, init);
    }));

    const r = await findOrCreateUserToken("blind@vexa.ai");
    // a prune that cannot see the fleet revokes NOTHING — it must never guess
    expect(r.ok).toBe(true);
    expect(fake.liveIds(user.id).length).toBe(6);
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
