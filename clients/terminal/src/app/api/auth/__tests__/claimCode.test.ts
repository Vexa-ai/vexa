/** POST /api/auth/claim-code — the claim screen's first step (M7).
 *
 *  A fresh instance admits nobody new and grants nobody the admin role without the one-time claim
 *  code admin-api logs at boot. This route checks a typed code with admin-api and, only for the live
 *  one, keeps it in an httpOnly cookie the following sign-in carries.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { POST as claimCode } from "../claim-code/route";

const CODE = "ABCD-EF01-JKMN-PQRS";
let asked: unknown[] = [];

function stub(answer: { status: number; body?: unknown } | "down") {
  asked = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/internal/admin-claim/check")) {
      asked.push(JSON.parse(String(init?.body ?? "{}")));
      if (answer === "down") throw new Error("ECONNREFUSED");
      return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status });
    }
    return new Response("nope", { status: 500 });
  }));
}

const post = (body: unknown) => claimCode(new Request("http://terminal.test/api/auth/claim-code", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
}) as unknown as import("next/server").NextRequest);

beforeEach(() => {
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubEnv("TERMINAL_URL", "https://terminal.test");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("the claim code", () => {
  it("the live code is kept for the sign-in that follows, httpOnly and only under /api/auth", async () => {
    stub({ status: 200, body: { valid: true } });
    const res = await post({ code: ` ${CODE} ` });
    expect(res.status).toBe(200);
    expect(asked).toEqual([{ claim_code: CODE }]);
    const cookie = res.cookies.get("vexa-claim-code");
    expect(cookie?.value).toBe(CODE);
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.path).toBe("/api/auth");
    expect(cookie?.secure).toBe(true);
  });

  it("any other code is refused at once and nothing is kept", async () => {
    stub({ status: 200, body: { valid: false } });
    const res = await post({ code: "WRONG-CODE" });
    expect(res.status).toBe(403);
    expect((await res.json()).error).toContain("admin-api log");
    expect(res.cookies.get("vexa-claim-code")).toBeUndefined();
  });

  it("an admin-api that cannot answer keeps nothing", async () => {
    stub("down");
    const res = await post({ code: CODE });
    expect(res.status).toBe(503);
    expect(res.cookies.get("vexa-claim-code")).toBeUndefined();
  });

  it("an empty code is not sent anywhere", async () => {
    stub({ status: 200, body: { valid: true } });
    expect((await post({ code: "  " })).status).toBe(400);
    expect((await post({})).status).toBe(400);
    expect(asked).toEqual([]);
  });
});
