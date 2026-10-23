// @vitest-environment node
/** S03T-4: the local-dev Minutes seams are not part of a production build, and none of them takes a
 *  person's identity from the display-only `vexa-user-info` cookie. */
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER } from "next/constants";
import config from "../../../next.config";

let jar = new Map<string, string>();
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (jar.has(name) ? { name, value: jar.get(name)! } : undefined) }),
}));

const MINUTES = fileURLToPath(new URL("../api/minutes", import.meta.url));

describe("the Minutes dev seams are not built into production", () => {
  it("only the development server reads a `route.dev.ts` as a route", () => {
    for (const phase of [PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER]) {
      const ext = config(phase).pageExtensions ?? [];
      expect(ext.some((e) => e.startsWith("dev.")), phase).toBe(false);
      expect(ext).toContain("ts");
    }
    expect(config(PHASE_DEVELOPMENT_SERVER).pageExtensions).toEqual(expect.arrayContaining(["dev.ts", "ts"]));
  });

  it("every Minutes seam is a `route.dev.ts` — none is a production route file", () => {
    const routes: string[] = [];
    for (const d of readdirSync(MINUTES)) {
      const dir = join(MINUTES, d);
      if (!statSync(dir).isDirectory()) continue;
      for (const f of readdirSync(dir)) if (/^route\./.test(f)) routes.push(`${d}/${f}`);
    }
    expect(routes.length).toBeGreaterThanOrEqual(4);
    expect(routes.filter((r) => !/\/route\.dev\.tsx?$/.test(r))).toEqual([]);
  });
});

describe("a seam acts only for the person the session cookie validates as", () => {
  let calls: string[];
  beforeEach(() => {
    jar = new Map();
    calls = [];
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_TERMINAL_MODE", "minutes");
    vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
    vi.stubEnv("VEXA_ADMIN_API_KEY", "admin-key");
    vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
    vi.stubEnv("VEXA_GATEWAY_PUBLIC_URL", "http://gw.test");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/internal/validate")) {
        const { token } = JSON.parse(String(init?.body));
        return token === "vxa_browser_seven"
          ? new Response(JSON.stringify({ user_id: 7, email: "seven@example.test", credential_class: "session" }), { status: 200 })
          : new Response("{}", { status: 401 });
      }
      if (/\/admin\/users\/\d+\/tokens/.test(url)) return new Response(JSON.stringify({ token: "minted" }), { status: 201 });
      if (url.startsWith("http://gw.test/meetings")) return new Response(JSON.stringify({ meetings: [{ id: 5, native_meeting_id: "abc-defg-hij" }] }), { status: 200 });
      return new Response("nope", { status: 404 });
    }));
  });
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  const body = () => new Request("http://t/api/minutes/ensure-meeting", { method: "POST", body: JSON.stringify({ platform: "google_meet", native: "abc-defg-hij" }) });

  it("a hand-written info cookie naming somebody mints nothing", async () => {
    const { POST } = await import("../api/minutes/ensure-meeting/route.dev");
    jar.set("vexa-user-info", JSON.stringify({ id: 99, email: "victim@example.test" }));
    for (const token of [undefined, "vxa_bot_forged"]) {
      if (token) jar.set("vexa-token", token);
      const r = await POST(body() as never);
      expect(r.status).toBe(401);
    }
    expect(calls.some((u) => /\/admin\/users\/\d+\/tokens/.test(u))).toBe(false);
  });

  it("positive control: the validated person's token is minted, whatever the info cookie says", async () => {
    const { POST } = await import("../api/minutes/ensure-meeting/route.dev");
    jar.set("vexa-token", "vxa_browser_seven");
    jar.set("vexa-user-info", JSON.stringify({ id: 99 }));
    const r = await POST(body() as never);
    expect(r.status).toBe(200);
    expect(calls.filter((u) => /\/admin\/users\/\d+\/tokens/.test(u))).toEqual([expect.stringContaining("/admin/users/7/tokens")]);
  });

  it("person-state reads nobody from the info cookie alone", async () => {
    const { GET } = await import("../api/minutes/person-state/route.dev");
    jar.set("vexa-user-info", JSON.stringify({ id: 99 }));
    const r = await GET({ nextUrl: new URL("http://t/api/minutes/person-state?key=onboarded") } as never);
    expect(r.status).toBe(401);
  });
});
