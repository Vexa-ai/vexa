/** WHO MAY SIGN IN — every terminal door against the instance's sign-in allow-list (Vexa-ai/vexa#1783).
 *
 *  Removing the company-layer gate removed the only thing that controlled who could sign in. Each
 *  door ends in find-or-create, so anybody who could finish one — a magic link to any mailbox they
 *  control, any Google account, any Microsoft account — got an account, an API token, agent turns
 *  on this instance's model credentials, and bot launches.
 *
 *  The rule (decided by admin-api ALONE, `POST /internal/signin-admission`): an EXISTING user, an
 *  ADMIN (claimed, or named by admin-api's VEXA_ADMIN_EMAILS), or an address on the allow-list; and,
 *  while nobody has claimed the instance and neither list is configured, the sign-in that will claim
 *  it. The fake admin-api below MODELS that rule over a small user table and allow-list,
 *  so these cases read as the behaviour a person meets. The rule itself, entry grammar and the
 *  env + settings merge included, is proven against the real code in admin-api's
 *  tests/test_signin_allow.py.
 *
 *  What is the terminal's own, and proven here: every door asks BEFORE it creates or sends
 *  anything; the email form's answer is identical for allowed and refused addresses (in time as well
 *  as in content); the terminal holds no list of its own (a VEXA_ADMIN_EMAILS in its environment
 *  changes nothing); and an admin-api that cannot answer refuses — fail closed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type MailArgs = { to: string; subject: string; text: string };
const sendMail = vi.fn(async (_opts: MailArgs): Promise<void> => {});
vi.mock("../mailer", () => ({ sendMail: (opts: MailArgs) => sendMail(opts) }));

let cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: (name: string, value: string) => { cookieJar[name] = value; },
    delete: (name: string) => { delete cookieJar[name]; },
  }),
}));

import { POST as requestLink } from "../request-link/route";
import { GET as redeem } from "../redeem/route";
import { POST as login } from "../login/route";
import { authOptions } from "../[...nextauth]/authOptions";
import { _settleLinkDeliveries } from "../linkDelivery";
import { signinAdmission } from "../adminApi";
import { _resetJtiLedger, mintMagicToken } from "../magicToken";
import { SIGNIN_NOT_ALLOWED, SIGNIN_UNAVAILABLE, signinErrorMessage } from "../../../signinRefusal";

// ── a fake admin-api that models the admission rule ───────────────────────────────────────────────

interface World {
  users: Set<string>;
  admins: Set<string>;
  adminEmails: string[];         // admin-api's VEXA_ADMIN_EMAILS
  allow: string[];               // the effective list: VEXA_SIGNIN_ALLOW + the signin.allow setting
  /** "down" = connection refused; "old" = an admin-api from before #1783 (no such route → 404);
   *  "garbled" = a 200 that does not literally say admitted:true. */
  admission?: "up" | "down" | "old" | "garbled";
}

let world: World;
let calls: string[];
let nextId = 100;

/** The live admin claim code this fake admin-api would have logged at boot. */
const CODE = "ABCD-EF01-JKMN-PQRS";

function admits(email: string, code?: string): { admitted: boolean; why: string } {
  const e = email.toLowerCase();
  if (world.admins.has(e)) return { admitted: true, why: "admin" };
  if (world.adminEmails.includes(e)) return { admitted: true, why: "admin-email" };
  if (world.users.has(e)) return { admitted: true, why: "existing-user" };
  const domain = "@" + e.split("@").pop();
  if (world.allow.includes(e) || world.allow.includes(domain)) return { admitted: true, why: "allow-list" };
  if (world.admins.size === 0 && !world.adminEmails.length && !world.allow.length && code === CODE) {
    return { admitted: true, why: "claim-code" };
  }
  return { admitted: false, why: "not-allowed" };
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function installAdminApi() {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    const method = init?.method || "GET";
    calls.push(`${method} ${u}`);
    if (u.includes("/internal/signin-admission")) {
      if (world.admission === "down") throw new Error("ECONNREFUSED");
      if (world.admission === "old") return new Response("Not Found", { status: 404 });
      if (world.admission === "garbled") return json({ allowed: true });
      const asked = JSON.parse(String(init?.body ?? "{}"));
      return json(admits(asked.email, asked.claim_code));
    }
    const byEmail = u.match(/\/admin\/users\/email\/(.+)$/);
    if (byEmail) {
      const e = decodeURIComponent(byEmail[1]).toLowerCase();
      return world.users.has(e) || world.admins.has(e) ? json({ id: 7, email: e }) : new Response("", { status: 404 });
    }
    if (method === "POST" && u.endsWith("/admin/users")) {
      const e = JSON.parse(String(init?.body ?? "{}")).email.toLowerCase();
      world.users.add(e);
      return json({ id: nextId++, email: e }, 201);
    }
    if (u.includes("/tokens")) return method === "POST" ? json({ token: "minted-tok" }) : json([]);
    if (u.includes("/internal/bootstrap-admin")) {
      const asked = JSON.parse(String(init?.body ?? "{}"));
      if (world.admins.size || world.adminEmails.length) return json({ claimed: false, admin_exists: true, why: "admin-exists" });
      if (asked.claim_code !== CODE) return json({ claimed: false, admin_exists: false, why: "bad-code" });
      world.admins.add("claimed@by-code");
      return json({ claimed: true, admin_exists: true, why: "claimed" });
    }
    if (u.includes("/internal/has-history")) return json({ has_history: true, sessions: 1, desk: "warm" });
    return new Response("nope", { status: 500 });
  }));
}

const created = () => calls.filter((c) => c.startsWith("POST") && c.endsWith("/admin/users"));
const minted = () => calls.filter((c) => c.startsWith("POST") && c.includes("/tokens"));

beforeEach(() => {
  world = { users: new Set(["member@example.com"]), admins: new Set(["boss@example.com"]), adminEmails: [], allow: ["@oenb.at", "alice@example.org"], admission: "up" };
  calls = [];
  cookieJar = {};
  sendMail.mockClear();
  sendMail.mockImplementation(async () => {});
  _resetJtiLedger();
  vi.stubEnv("NEXTAUTH_SECRET", "test-signing-secret-0123456789abcdef");
  vi.stubEnv("NEXTAUTH_URL", "https://terminal.test");
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-secret");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("VEXA_ADMIN_EMAILS", "");
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "info").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  installAdminApi();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

// ── the three doors, driven as a person meets them ───────────────────────────────────────────────

function linkReq(email: string) {
  return { json: async () => ({ email, next: "/" }), headers: new Headers({ host: "terminal.test" }) } as unknown as import("next/server").NextRequest;
}

async function askForLink(email: string) {
  const res = await requestLink(linkReq(email));
  await _settleLinkDeliveries();
  return res;
}

function redeemReq(token: string) {
  const url = new URL("https://terminal.test/api/auth/redeem");
  url.searchParams.set("t", token);
  url.searchParams.set("next", "/?ask=catch-up");
  return { nextUrl: url, url: url.toString() } as unknown as import("next/server").NextRequest;
}

function clickLinkFor(email: string) {
  const m = mintMagicToken(email);
  if (!m.ok) throw new Error("mint failed");
  return redeem(redeemReq(m.token));
}

const oauth = (email: string, provider: "google" | "microsoft" = "google") =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (authOptions.callbacks!.signIn as any)({ user: { email, name: "N" }, account: { provider } });

describe("an unknown address is refused at every door, before anything exists", () => {
  it("the emailed link: no mail is sent", async () => {
    const res = await askForLink("stranger@evil.example");
    expect(res.status).toBe(200);
    expect(sendMail).not.toHaveBeenCalled();
    expect(created()).toHaveLength(0);
  });

  it("the redeem: refused with the shared sentence, no account and no token", async () => {
    const res = await clickLinkFor("stranger@evil.example");
    expect(res.status).toBe(403);
    expect(await res.text()).toContain(SIGNIN_NOT_ALLOWED);
    expect(res.cookies.get("vexa-token")).toBeUndefined();
    expect(created()).toHaveLength(0);
    expect(minted()).toHaveLength(0);
  });

  it("Google: refused to the sign-in card with a code the card turns into the same sentence", async () => {
    const r = await oauth("stranger@gmail.com", "google");
    expect(r).toBe("/?error=SigninNotAllowed");
    expect(signinErrorMessage(new URL(r, "https://t").searchParams.get("error"))).toBe(SIGNIN_NOT_ALLOWED);
    expect(cookieJar["vexa-token"]).toBeUndefined();
    expect(created()).toHaveLength(0);
  });

  it("Microsoft: the same, whichever tenant vouched for the address", async () => {
    expect(await oauth("stranger@outlook.com", "microsoft")).toBe("/?error=SigninNotAllowed");
    expect(cookieJar["vexa-token"]).toBeUndefined();
    expect(created()).toHaveLength(0);
  });

  it("the dev login door asks too", async () => {
    vi.stubEnv("NODE_ENV", "development");
    const res = await login({ json: async () => ({ email: "stranger@evil.example" }) } as unknown as import("next/server").NextRequest);
    expect(res.status).toBe(403);
    expect((await res.json()).error).toBe(SIGNIN_NOT_ALLOWED);
    expect(created()).toHaveLength(0);
  });

  it("every door asked admin-api the ADMISSION question, and nothing after it", async () => {
    await clickLinkFor("stranger@evil.example");
    expect(calls).toEqual(["POST http://admin.test/internal/signin-admission"]);
  });
});

describe("who IS admitted", () => {
  it("an existing user — upgrading locks nobody out, at every door", async () => {
    await askForLink("member@example.com");
    expect(sendMail).toHaveBeenCalledTimes(1);
    expect((await clickLinkFor("member@example.com")).status).toBe(302);
    expect(await oauth("Member@Example.com")).toBe(true);
    expect(created()).toHaveLength(0); // found, not created
  });

  it("an address on the allow-list by DOMAIN entry, and by EXACT entry", async () => {
    const domain = await clickLinkFor("anna@oenb.at");
    expect(domain.status).toBe(302);
    expect(domain.cookies.get("vexa-token")?.value).toBe("minted-tok");
    expect(await oauth("alice@example.org", "microsoft")).toBe(true);
    expect(created()).toHaveLength(2); // new people on the list get their account at first sign-in
  });

  it("the claimed admin", async () => {
    expect(await oauth("boss@example.com")).toBe(true);
  });

  it("an address admin-api's VEXA_ADMIN_EMAILS names — on admin-api's word", async () => {
    world.adminEmails = ["owner@example.com"];
    expect(await signinAdmission("Owner@Example.com")).toEqual({ admitted: true, why: "admin-email" });
    expect(calls).toEqual(["POST http://admin.test/internal/signin-admission"]);
  });

  it("the terminal decides nothing itself: a VEXA_ADMIN_EMAILS in ITS environment admits nobody", async () => {
    vi.stubEnv("VEXA_ADMIN_EMAILS", "stranger@evil.example");
    expect(await signinAdmission("stranger@evil.example")).toEqual({ admitted: false, why: "not-allowed" });
    expect(await oauth("stranger@evil.example")).toBe("/?error=SigninNotAllowed");
    expect(created()).toHaveLength(0);
  });
});

describe("the email form never reveals who is allowed", () => {
  it("allowed and refused addresses get byte-identical responses", async () => {
    const allowed = await askForLink("member@example.com");
    const refused = await askForLink("stranger@evil.example");
    expect(refused.status).toBe(allowed.status);
    expect(await refused.text()).toBe(await allowed.text());
    expect([...refused.headers.entries()]).toEqual([...allowed.headers.entries()]);
    // …and only one of them was mailed
    expect(sendMail).toHaveBeenCalledTimes(1);
    expect(sendMail.mock.calls[0][0].to).toBe("member@example.com");
  });

  it("the response does not wait for the admission question or the mail — no timing oracle", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const real = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/internal/signin-admission")) await gate;
      return real(url, init);
    }));
    const res = await requestLink(linkReq("member@example.com"));
    // answered while admin-api has not replied and nothing has been mailed
    expect(res.status).toBe(200);
    expect(sendMail).not.toHaveBeenCalled();
    release();
    await _settleLinkDeliveries();
    expect(sendMail).toHaveBeenCalledTimes(1);
  });
});

describe("FAIL CLOSED — an admin-api that cannot answer admits nobody new", () => {
  for (const mode of ["down", "old", "garbled"] as const) {
    it(`admission ${mode}: no mail, no account at redeem, OAuth refused as unavailable`, async () => {
      world.admission = mode;
      const res = await askForLink("member@example.com"); // even an existing user: nothing can be decided
      expect(res.status).toBe(200);
      expect(sendMail).not.toHaveBeenCalled();

      const page = await clickLinkFor("member@example.com");
      expect(page.cookies.get("vexa-token")).toBeUndefined();
      expect(minted()).toHaveLength(0);
      expect(created()).toHaveLength(0);

      const r = await oauth("member@example.com");
      expect(cookieJar["vexa-token"]).toBeUndefined();
      if (mode === "garbled") {
        // a 200 that does not say admitted:true is a refusal, not an outage
        expect(page.status).toBe(403);
        expect(r).toBe("/?error=SigninNotAllowed");
      } else {
        expect(page.status).toBe(503);
        expect(await page.text()).toContain("unavailable");
        expect(r).toBe("/?error=SigninUnavailable");
        expect(signinErrorMessage("SigninUnavailable")).toBe(SIGNIN_UNAVAILABLE);
      }
    });
  }

  it("an unconfigured internal edge is the same refusal", async () => {
    vi.stubEnv("VEXA_INTERNAL_API_SECRET", "");
    expect((await signinAdmission("member@example.com")).admitted).toBe(false);
    expect(await oauth("member@example.com")).toBe("/?error=SigninUnavailable");
  });
});

describe("the first admin claim still works", () => {
  it("no code, no claim: a fresh instance admits nobody new, at any door", async () => {
    world.admins.clear();
    world.users.clear();
    world.allow = [];
    await askForLink("first-visitor@anywhere.example");
    expect(sendMail).not.toHaveBeenCalled();
    expect((await clickLinkFor("first-visitor@anywhere.example")).status).toBe(403);
    expect(await oauth("first-visitor@anywhere.example")).toBe("/?error=SigninNotAllowed");
    expect(created()).toHaveLength(0);
    expect(calls.some((c) => c.includes("/internal/bootstrap-admin"))).toBe(false);
  });

  it("with the claim code entered on the claim screen, the first sign-in is admitted and claims the role", async () => {
    world.admins.clear();
    world.users.clear();
    world.allow = [];
    cookieJar["vexa-claim-code"] = CODE;
    await askForLink("founder@newco.example");
    expect(sendMail).toHaveBeenCalledTimes(1);           // the link is mailed…
    const res = await clickLinkFor("founder@newco.example");
    expect(res.status).toBe(302);                        // …and redeemed in this browser
    expect(res.cookies.get("vexa-token")?.value).toBe("minted-tok");
    expect(created()).toHaveLength(1);
    const claim = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls
      .find(([u]) => String(u).includes("/internal/bootstrap-admin"));
    expect(JSON.parse(String(claim![1].body))).toMatchObject({ claim_code: CODE });
    expect(world.admins.size).toBe(1);
  });

  it("a wrong code opens nothing", async () => {
    world.admins.clear();
    world.users.clear();
    world.allow = [];
    cookieJar["vexa-claim-code"] = "WRONG-CODE";
    expect(await oauth("first-visitor@anywhere.example")).toBe("/?error=SigninNotAllowed");
    expect(created()).toHaveLength(0);
  });

  it("…but not when admin-api's VEXA_ADMIN_EMAILS names the admins: the claim is off, so is that door", async () => {
    world.admins.clear();
    world.allow = [];
    world.adminEmails = ["owner@example.com"];
    expect(await oauth("stranger@evil.example")).toBe("/?error=SigninNotAllowed");
    expect(created()).toHaveLength(0);
    // the named admin still gets in
    expect(await oauth("owner@example.com")).toBe(true);
  });

  it("…nor when an allow-list is configured: only its addresses get in", async () => {
    world.admins.clear();
    world.users.clear();
    expect(await oauth("stranger@evil.example")).toBe("/?error=SigninNotAllowed");
    expect(await oauth("anna@oenb.at")).toBe(true);
  });
});
