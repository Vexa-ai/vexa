/** POST /api/auth/claim-admin — the claim reachable by a session that already exists.
 *
 *  ⚠ WHY THE ROUTE EXISTS (observed live 2026-09-02, 08:48Z). The admin role could only be claimed
 *  inside findOrCreateUserToken, i.e. only while walking through a sign-in door. An instance whose
 *  sessions predate the gate therefore had no reachable claim at all — the founder's cookie was
 *  valid, admin_exists was false, and a cookie never traverses a sign-in door twice. The instance
 *  said "not set up" forever with nothing able to set it up.
 *
 *  Three properties, in descending order of what they cost when wrong:
 *    1. IDENTITY comes from the validated `vexa-token` ONLY. `vexa-user-info` is client-forgeable
 *       (httpOnly stops a script, not a curl), and this route grants the highest privilege the
 *       product has.
 *    2. It FAILS CLOSED — the opposite direction from the rest of the gate — because guessing wrong
 *       here grants admin rather than merely opening a door.
 *    3. It never TRANSFERS the role: an instance that already has an admin is refused.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: (name: string, value: string) => { cookieJar[name] = value; },
    delete: (name: string) => { delete cookieJar[name]; },
  }),
}));

import { POST as claimAdmin } from "../claim-admin/route";

/** Every scaffold-mint body agent-api saw — which record was asked for is the whole of F42. */
const minted: Record<string, unknown>[] = [];

/** admin-api double: the validate oracle, the instance probe, and the bootstrap-admin write. */
function stubAdminApi(opts: {
  validate?: { status: number; body?: unknown };
  adminExists?: boolean;
  instanceFails?: boolean;
  bootstrap?: { status: number; body?: unknown };
  mint?: { status: number; body?: unknown };
  mintThrows?: boolean;
  globalSetup?: "completed" | "missing";
  hasHistory?: boolean;
  recordFails?: boolean;
}) {
  const calls: string[] = [];
  minted.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    calls.push(`${init?.method || "GET"} ${u}`);
    // #1591: a first-visit arrival is minted only for somebody with nothing to return to, so every
    // door now asks agent-api before it mints. These tests are about WHICH record is asked for, so
    // the claimer is a stranger unless a case says otherwise.
    if (u.includes("/internal/has-history")) {
      return new Response(JSON.stringify({ has_history: opts.hasHistory === true, sessions: 0, desk: "new" }),
                          { status: 200 });
    }
    if (u.includes("/internal/validate")) {
      const v = opts.validate ?? { status: 200, body: { user_id: 11, email: "dmitry@vexa.ai", is_admin: false } };
      return new Response(JSON.stringify(v.body ?? {}), { status: v.status });
    }
    if (u.includes("/internal/instance")) {
      if (opts.instanceFails) throw new Error("ECONNREFUSED");
      return new Response(JSON.stringify({ admin_exists: opts.adminExists ?? false, global_setup: opts.globalSetup ?? "missing" }), { status: 200 });
    }
    if (u.includes("/internal/bootstrap-admin")) {
      const b = opts.bootstrap ?? { status: 200, body: { claimed: true } };
      return new Response(JSON.stringify(b.body ?? {}), { status: b.status });
    }
    // The platform-settings store SetupGate reads its resume state out of (#1609). `400` is the
    // shape admin-api answers a write it understood nothing of.
    if (u.includes("/internal/settings/")) {
      if (opts.recordFails) return new Response(JSON.stringify({ detail: "nothing recognised" }), { status: 400 });
      return new Response(JSON.stringify({ key: "setup", value: { global: "handoff" } }), { status: 200 });
    }
    if (u.includes("/internal/scaffolds")) {
      minted.push(JSON.parse(String(init?.body ?? "{}")));
      if (opts.mintThrows) throw new Error("ECONNREFUSED");
      const m = opts.mint ?? { status: 201, body: { id: "SCAF1", url: "https://app.test/?s=SCAF1" } };
      return new Response(JSON.stringify(m.body ?? {}), { status: m.status });
    }
    return new Response("nope", { status: 500 });
  }));
  return calls;
}

beforeEach(() => {
  cookieJar = { "vexa-token": "a-real-session-token" };
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-key");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("VEXA_ADMIN_EMAILS", "");
  vi.stubEnv("AGENT_API_URL", "http://agent.test");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the happy path", () => {
  it("claims with the id the ORACLE returned, never one from a cookie", async () => {
    // The info cookie says user 999; the oracle says 11. Only 11 may reach bootstrap-admin.
    cookieJar["vexa-user-info"] = JSON.stringify({ id: 999, email: "attacker@example.com" });
    const calls = stubAdminApi({});
    const spy = vi.mocked(globalThis.fetch);

    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      success: true, claimed: true, email: "dmitry@vexa.ai",
      url: "https://app.test/?s=SCAF1", scaffold: "SCAF1",
    });

    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(true);
    const write = spy.mock.calls.find(([u]) => String(u).includes("/internal/bootstrap-admin"));
    expect(JSON.parse(String((write![1] as RequestInit).body))).toEqual({ user_id: 11 });
  });
});

describe("who may claim", () => {
  it("no cookie → 401, and nothing is asked of admin-api", async () => {
    cookieJar = {};
    const calls = stubAdminApi({});
    const res = await claimAdmin();
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("a token the oracle REFUSES → 401, no claim written", async () => {
    const calls = stubAdminApi({ validate: { status: 401 } });
    const res = await claimAdmin();
    expect(res.status).toBe(401);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("an unreachable oracle FAILS CLOSED — 503, no claim written", async () => {
    // The opposite direction from instanceState() on purpose: a wrong guess here grants admin.
    const calls = stubAdminApi({ validate: { status: 502 } });
    const res = await claimAdmin();
    expect(res.status).toBe(503);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("an unreachable instance probe also fails closed rather than granting", async () => {
    // instanceState() fails safe towards admin_exists:true, which lands here as "already claimed" —
    // i.e. the fail-safe that opens sign-in is the one that REFUSES a claim. Both directions are the
    // cautious one for their own consequence.
    const calls = stubAdminApi({ instanceFails: true });
    const res = await claimAdmin();
    expect(res.status).toBe(409);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });
});

describe("it can never transfer the role", () => {
  it("an instance that already has an admin is refused, and told to reload", async () => {
    const calls = stubAdminApi({ adminExists: true });
    const res = await claimAdmin();
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({
      error: "This instance already has an administrator.", admin_exists: true, reload: true,
    });
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("losing admin-api's race is reported honestly, not as a claim", async () => {
    // admin-api serialises concurrent claims; the loser gets claimed:false. An admin exists either
    // way and the page reloads onto the right screen — but this session did not claim it.
    stubAdminApi({ bootstrap: { status: 200, body: { claimed: false } } });
    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect((await res.json()).claimed).toBe(false);
  });

  it("a bootstrap write that fails is surfaced, not swallowed", async () => {
    stubAdminApi({ bootstrap: { status: 500, body: { detail: "boom" } } });
    const res = await claimAdmin();
    expect(res.status).toBe(500);
    expect((await res.json()).error).toContain("Could not claim this instance");
  });
});


/** THE CONVERSATION, NOT JUST THE ROLE (F26).
 *
 *  The hand-off used to live in `localStorage`: clear it, or open the instance in a second browser,
 *  and the admin landed in a Personal chat on the generic greeting while the setup marker already
 *  said "handoff", so nothing re-opened the conversation. Verified live 2026-09-02. The claim now
 *  mints the admin-setup scaffold — the conversation as a SERVER record — and hands back the url. */
describe("the setup conversation is minted, not stashed", () => {
  it("mints an admin-setup scaffold for the ORACLE's address, with no prompt text and no mount list", async () => {
    stubAdminApi({});
    const spy = vi.mocked(globalThis.fetch);
    await claimAdmin();

    const mint = spy.mock.calls.find(([u]) => String(u).includes("/internal/scaffolds"));
    expect(mint).toBeDefined();
    const body = JSON.parse(String((mint![1] as RequestInit).body));
    expect(body).toEqual({
      who: "dmitry@vexa.ai",
      kind: "admin-setup",
      opening: "setup-global",
      provenance: { flow: "admin-claim", step: "claim-admin", minted_by: "11" },
    });
    // `workspaces`, `tabs` and `focus` are ABSENT, not empty: the server derives `_global` + this
    // admin's own desk from the address and takes the tabs from the preset's frontmatter. Sending
    // them here would be a second spelling of a rule that already has one.
    expect("workspaces" in body).toBe(false);
    expect("tabs" in body).toBe(false);
    // And nothing carries prompt text — the record behind the url is as text-free as the url.
    expect(JSON.stringify(body)).not.toMatch(/\[setup-global\]/);
  });

  it("mints on the INTERNAL tier, which a browser can never reach", async () => {
    stubAdminApi({});
    const spy = vi.mocked(globalThis.fetch);
    await claimAdmin();
    const mint = spy.mock.calls.find(([u]) => String(u).includes("/internal/scaffolds"));
    const headers = (mint![1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-Internal-Secret"]).toBe("internal-secret");
  });

  it("a failed mint still reports the role as claimed — and says the conversation is what is missing", async () => {
    // The role write already happened and is not undone by this. A caller told only "failed" would
    // reasonably re-claim; one told only "success" would navigate to a chat that does not exist.
    stubAdminApi({ mint: { status: 503 } });
    const res = await claimAdmin();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.success).toBe(true);
    expect(body.claimed).toBe(true);
    expect(body.url).toBe("/");
    expect(body.scaffold_error).toMatch(/administrator, but the setup conversation could not be opened/);
  });

  it("an unreachable agent-api is the same story, not a crash", async () => {
    stubAdminApi({ mintThrows: true });
    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect((await res.json()).scaffold_error).toBeTruthy();
  });

  it("does not mint when the claim itself was refused", async () => {
    // An instance that already has an admin gets 409 and no scaffold: minting one for somebody who
    // is not the administrator would compose a stranger's first turn over the company layer.
    stubAdminApi({ adminExists: true });
    const spy = vi.mocked(globalThis.fetch);
    const res = await claimAdmin();
    expect(res.status).toBe(409);
    expect(spy.mock.calls.some(([u]) => String(u).includes("/internal/scaffolds"))).toBe(false);
  });
});

/** F42 — WHICH CONVERSATION A CLAIM ARRIVES IN. Founder ruling 2026-09-02.
 *
 *  This route exists for the LATE claim: an instance that acquired its admin after the fact. Such an
 *  instance may already have its company layer written — and offering the setup conversation to it
 *  says the product does not know its own state. The founder read exactly that as the product being
 *  wrong about him: an admin-only "Organisation setup" card in front of somebody who needed nothing
 *  of the sort. */
describe("F42 — the arrival depends on whether the instance is already set up", () => {
  it("company layer MISSING → the setup conversation", async () => {
    stubAdminApi({ globalSetup: "missing" });
    await claimAdmin();
    expect(minted.map((m) => m.kind)).toEqual(["admin-setup"]);
    expect(minted[0].opening).toBe("setup-global");
  });

  it("company layer COMPLETED → an ordinary first visit, not setup again", async () => {
    stubAdminApi({ globalSetup: "completed" });
    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect(minted.map((m) => m.kind)).toEqual(["first-visit"]);
    expect(minted[0].opening).toBe("first-visit");
  });

  it("either way the client composes NOTHING — no workspaces, no tabs, no prompt text", async () => {
    for (const globalSetup of ["missing", "completed"] as const) {
      stubAdminApi({ globalSetup });
      await claimAdmin();
      for (const forbidden of ["workspaces", "tabs", "focus", "prompt", "opening_text"]) {
        expect(minted[0]).not.toHaveProperty(forbidden);
      }
    }
  });
});

/** #1609 — ONE CLAIM, ONE SETUP CHAT.
 *
 *  The claim minted the setup conversation and the client followed `/?s=<id>` — and nothing wrote
 *  the hand-off marker, because only `SetupGate` had ever written it. So the gate mounted in the
 *  document the arrival had just landed in, read the marker as absent, concluded that nobody had
 *  opened the setup conversation, and opened one on top of it. The founder's own blank-instance
 *  sign-in never hit this (the claim happens inside the sign-in, so the card is never shown); an
 *  instance whose admin claims through the card always did.
 *
 *  The rule: whoever OPENS the conversation records that they did, in the store the gate reads.
 *  This route is now an opener. The gate's half — a recorded hand-off resumes as the corner card
 *  and opens nothing — is in `app/__tests__/setupGate.test.tsx`, which owns that harness. */
describe("the hand-off is recorded where SetupGate reads it", () => {
  it("writes `setup.global = handoff` to the platform-settings store, on the internal tier", async () => {
    stubAdminApi({});
    const spy = vi.mocked(globalThis.fetch);
    await claimAdmin();

    const put = spy.mock.calls.find(([u, i]) =>
      String(u).includes("/internal/settings/setup") && (i as RequestInit)?.method === "PUT");
    expect(put).toBeDefined();
    // Exactly one field, and one admin-api's `_SETUP_FIELDS` knows. A field it does not know is
    // dropped in silence — which is how this very marker vanished on 2026-09-02 while answering 200.
    expect(JSON.parse(String((put![1] as RequestInit).body))).toEqual({ global: "handoff" });
    // Internal tier, like the mint beside it: a browser can never reach this edge.
    expect(((put![1] as RequestInit).headers as Record<string, string>)["X-Internal-Secret"]).toBe("internal-secret");
  });

  it("records it AFTER the conversation exists, never before", async () => {
    // The marker asserts that a conversation is open. Written first, a mint that then failed would
    // leave the gate resuming as the corner card with nothing underneath it — a worse dead end than
    // the one this route exists to open.
    const calls = stubAdminApi({});
    await claimAdmin();
    const mint = calls.findIndex((c) => c.includes("/internal/scaffolds"));
    const record = calls.findIndex((c) => c.includes("/internal/settings/setup"));
    expect(mint).toBeGreaterThanOrEqual(0);
    expect(record).toBeGreaterThan(mint);
  });

  it("records NOTHING when the mint failed — the gate must still open the conversation itself", async () => {
    const calls = stubAdminApi({ mint: { status: 503 } });
    const res = await claimAdmin();
    expect((await res.json()).scaffold_error).toBeTruthy();
    expect(calls.some((c) => c.includes("/internal/settings/setup"))).toBe(false);
  });

  it("records nothing when the claim itself was refused", async () => {
    const calls = stubAdminApi({ adminExists: true });
    expect((await claimAdmin()).status).toBe(409);
    expect(calls.some((c) => c.includes("/internal/settings/setup"))).toBe(false);
  });

  it("a record that FAILS does not cost the admin the conversation", async () => {
    // The role is claimed and the chat exists. A marker that did not stick costs exactly the extra
    // chat this fixes; withholding the url over it would cost them the conversation itself.
    stubAdminApi({ recordFails: true });
    const res = await claimAdmin();
    expect(res.status).toBe(200);
    expect((await res.json()).url).toBe("https://app.test/?s=SCAF1");
  });

  it("records the F42 first visit too — no setup chat may open over that arrival either", async () => {
    // An instance that is already set up gets an ordinary first visit rather than the setup
    // conversation. It is still an arrival this claim delivered them into, so the gate must not open
    // a setup conversation on top of it — and on that instance it would be a setup conversation
    // offered to somebody who needs none at all, which is the exact thing F42 settled.
    const calls = stubAdminApi({ globalSetup: "completed" });
    await claimAdmin();
    expect(minted.map((m) => m.kind)).toEqual(["first-visit"]);
    expect(calls.some((c) => c === "PUT http://admin.test/internal/settings/setup")).toBe(true);
  });
});
