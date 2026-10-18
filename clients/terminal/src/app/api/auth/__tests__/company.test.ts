import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** /api/auth/company — the Company profile switch (ADR-0049). An instance administrator's tab takes
 *  the admin account's session and gives it back. Pinned here: who may switch (the oracle's
 *  `is_admin`, never the admin account itself), that the minted session counts only when the oracle
 *  says it IS the admin account, that the person's own token comes back untouched, and that the
 *  company token is revoked on the way back. */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
  }),
}));

import { POST as companyRoute } from "../company/route";
import { GET as meRoute } from "../me/route";

type Who = { user_id: number; email: string; is_admin?: boolean; admin_account?: boolean };
const PEOPLE: Record<string, Who> = {
  "tok-admin": { user_id: 7, email: "founder@acme.example", is_admin: true },
  "tok-plain": { user_id: 8, email: "someone@acme.example", is_admin: false },
  "tok-company": { user_id: 99, email: "company@acme.example", is_admin: false, admin_account: true },
  "tok-wrong": { user_id: 99, email: "company@acme.example", is_admin: false, admin_account: false },
};

let mintAnswer = "tok-company";
let calls: { url: string; method: string }[] = [];

function stub() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const u = String(url);
    calls.push({ url: u, method: init.method || "GET" });
    if (u.endsWith("/internal/validate")) {
      const who = PEOPLE[JSON.parse(String(init.body)).token];
      return who ? new Response(JSON.stringify(who), { status: 200 }) : new Response("{}", { status: 401 });
    }
    if (u.includes("/admin/users/email/")) return new Response(JSON.stringify({ id: 99, email: "company@acme.example" }), { status: 200 });
    if (u.includes("/admin/users/99/tokens") && init.method === "POST") {
      return new Response(JSON.stringify({ id: 555, user_id: 99, scopes: ["bot"], token: mintAnswer }), { status: 201 });
    }
    if (u.includes("/admin/tokens/")) return new Response(null, { status: 204 });
    return new Response("{}", { status: 404 });
  }));
}

function setCookies(res: Response): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of res.headers.getSetCookie()) {
    const [pair] = line.split(";");
    const i = pair.indexOf("=");
    out[pair.slice(0, i)] = decodeURIComponent(pair.slice(i + 1));
  }
  return out;
}

const post = (to: string) => companyRoute(new Request("http://t/api/auth/company", { method: "POST", body: JSON.stringify({ to }) }));

beforeEach(() => {
  cookieJar = {};
  mintAnswer = "tok-company";
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-key";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  process.env.VEXA_ADMIN_ACCOUNT = "company@acme.example";
  vi.spyOn(console, "log").mockImplementation(() => undefined);
  stub();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  for (const k of ["VEXA_ADMIN_API_URL", "VEXA_ADMIN_API_KEY", "VEXA_INTERNAL_API_SECRET", "VEXA_ADMIN_ACCOUNT"]) delete process.env[k];
});

describe("/api/auth/company", () => {
  it("an instance administrator switches to the company and back", async () => {
    cookieJar = { "vexa-token": "tok-admin", "vexa-user-info": JSON.stringify({ email: "founder@acme.example", name: "Founder" }) };
    const res = await post("company");
    expect(res.status).toBe(200);
    const set = setCookies(res);
    expect(set["vexa-token"]).toBe("tok-company");
    expect(set["vexa-person-token"]).toBe("tok-admin");
    expect(set["vexa-company-token-id"]).toBe("555");
    // The minted session names whose Company session it is.
    expect(calls.some((c) => c.url.includes("/admin/users/99/tokens") && c.url.includes("name=company%3A7"))).toBe(true);

    cookieJar = { ...cookieJar, ...set };
    const me = await (await meRoute()).json();
    expect(me.admin_account).toBe(true);
    expect(me.company).toEqual({ person: { email: "founder@acme.example", name: "Founder" } });
    expect(me.can_work_as_company).toBe(false);

    const back = await post("me");
    expect(back.status).toBe(200);
    const restored = setCookies(back);
    expect(restored["vexa-token"]).toBe("tok-admin");
    expect(restored["vexa-person-token"]).toBe("");
    expect(calls.some((c) => c.url.endsWith("/admin/tokens/555") && c.method === "DELETE")).toBe(true);
  });

  it("the switch is offered to an instance administrator only", async () => {
    cookieJar = { "vexa-token": "tok-admin" };
    expect((await (await meRoute()).json()).can_work_as_company).toBe(true);
    cookieJar = { "vexa-token": "tok-plain" };
    expect((await (await meRoute()).json()).can_work_as_company).toBe(false);
    delete process.env.VEXA_ADMIN_ACCOUNT;
    cookieJar = { "vexa-token": "tok-admin" };
    expect((await (await meRoute()).json()).can_work_as_company).toBe(false);
  });

  it("an everyday account that is not an administrator is refused, and nothing is minted", async () => {
    cookieJar = { "vexa-token": "tok-plain" };
    expect((await post("company")).status).toBe(403);
    expect(calls.some((c) => c.url.includes("/tokens"))).toBe(false);
  });

  it("a minted token the oracle does not call the admin account is revoked and refused", async () => {
    mintAnswer = "tok-wrong";
    cookieJar = { "vexa-token": "tok-admin" };
    const res = await post("company");
    expect(res.status).toBe(503);
    expect(setCookies(res)["vexa-token"]).toBeUndefined();
    expect(calls.some((c) => c.url.endsWith("/admin/tokens/555") && c.method === "DELETE")).toBe(true);
  });

  it("the admin account is created the first time an administrator opens it", async () => {
    let created = false;
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).includes("/admin/users/email/") && !created) return new Response("{}", { status: 404 });
      if (String(url).endsWith("/admin/users") && init.method === "POST") {
        created = true;
        return new Response(JSON.stringify({ id: 99, email: "company@acme.example" }), { status: 201 });
      }
      return base(url, init);
    }));
    cookieJar = { "vexa-token": "tok-admin" };
    expect((await post("company")).status).toBe(200);
    expect(created).toBe(true);
  });

  it("no admin account named → refused before anything is looked up", async () => {
    delete process.env.VEXA_ADMIN_ACCOUNT;
    cookieJar = { "vexa-token": "tok-admin" };
    expect((await post("company")).status).toBe(409);
    expect(calls.some((c) => c.url.includes("/admin/users"))).toBe(false);
  });

  it("switching back without a Company session is refused", async () => {
    cookieJar = { "vexa-token": "tok-admin" };
    expect((await post("me")).status).toBe(409);
  });

  it("a Company session that ran out returns the person to their own session", async () => {
    cookieJar = { "vexa-token": "expired", "vexa-person-token": "tok-admin", "vexa-person-info": "{}" };
    const res = await meRoute();
    expect(res.status).toBe(401);
    expect(setCookies(res)["vexa-token"]).toBe("tok-admin");
  });
});
