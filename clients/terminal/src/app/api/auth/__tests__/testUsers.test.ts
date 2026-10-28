import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** "Test users…" — app.dev tooling (#1783). A one-time sign-in link for an `@example.test` account,
 *  for a signed-in instance administrator, on a deployment that switched it on. Nothing else. */
let cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (n: string) => (cookieJar[n] !== undefined ? { name: n, value: cookieJar[n] } : undefined) }),
}));

import { GET as listRoute, POST as createRoute } from "../test-users/route";
import { POST as linkRoute } from "../test-users/link/route";
import { testAddressFor, isTestAddress } from "../testUsers";

let admin = true;
let created: string[] = [];
beforeEach(() => {
  cookieJar = { "vexa-token": "tok" };
  admin = true; created = [];
  process.env.VEXA_DEV_TEST_USERS = "1";
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "k";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  process.env.NEXTAUTH_URL = "https://app.example.test";
  process.env.NEXTAUTH_SECRET = "x".repeat(48);
  vi.spyOn(console, "info").mockImplementation(() => undefined);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/internal/validate")) return new Response(JSON.stringify({ user_id: 1, email: "founder@vexa.ai", is_admin: admin }), { status: 200 });
    if (u.endsWith("/admin/users/test-users")) return new Response(JSON.stringify([{ id: 5, email: "tester-ann@example.test" }]), { status: 200 });
    if (u.endsWith("/admin/users") && init?.method === "POST") {
      const email = JSON.parse(String(init.body)).email; created.push(email);
      return new Response(JSON.stringify({ id: 6, email }), { status: 201 });
    }
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); delete process.env.VEXA_DEV_TEST_USERS; });

const post = (body: unknown) => new Request("https://app.example.test/x", { method: "POST", body: JSON.stringify(body) }) as never;

describe("test users", () => {
  it("an admin lists, creates tester-<slug>@example.test, and gets a one-time redeem link", async () => {
    expect(await (await listRoute()).json()).toEqual({ users: [{ id: "5", email: "tester-ann@example.test", name: null }] });
    const made = await createRoute(post({ name: "Bo Smith" }));
    expect(made.status).toBe(200);
    expect(created).toEqual(["tester-bo-smith@example.test"]);
    const link = await linkRoute(post({ email: "tester-ann@example.test" }));
    expect(link.status).toBe(200);
    const { url } = await link.json();
    expect(url).toMatch(/^https:\/\/app\.example\.test\/api\/auth\/redeem\?t=/);
  });

  it("is refused when the deployment has not switched it on", async () => {
    delete process.env.VEXA_DEV_TEST_USERS;
    expect((await listRoute()).status).toBe(404);
    expect((await linkRoute(post({ email: "tester-ann@example.test" }))).status).toBe(404);
  });

  it("is refused to anyone but an instance administrator", async () => {
    admin = false;
    expect((await linkRoute(post({ email: "tester-ann@example.test" }))).status).toBe(403);
    expect((await createRoute(post({ name: "x" }))).status).toBe(403);
    cookieJar = {};
    expect((await listRoute()).status).toBe(401);
  });

  it("never mints a link for an address outside the test domain", async () => {
    for (const email of ["founder@vexa.ai", "x@example.test.evil.com", "a@EXAMPLE.TEST.co", 42]) {
      expect((await linkRoute(post({ email }))).status).toBe(400);
    }
    expect(isTestAddress("Tester-Ann@Example.Test")).toBe(true);
    expect(testAddressFor("  Ann Lee ")).toBe("tester-ann-lee@example.test");
    expect(testAddressFor("!!!")).toBeNull();
  });
});
