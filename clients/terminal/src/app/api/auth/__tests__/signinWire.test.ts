/** The terminal reads admin-api's admission answers through signin.v1 (S10).
 *
 *  `signinWire.ts` is generated from core/identity/contracts/signin.v1/signin.schema.json, as is
 *  admin-api's `signin_wire.py`, and `validate.mjs --check` (gate:schema) fails when either drifts.
 *  These cases drive the real `signinAdmission` / `claimAdminRole` with every golden the contract
 *  pins, so a reason admin-api can give is a reason this side admits — not one it quietly refuses.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { claimAdminRole, signinAdmission } from "../adminApi";
import { ADMITTED_REASONS, CLAIM_REASONS } from "../signinWire";

const GOLDEN = join(__dirname, "../../../../../../../core/identity/contracts/signin.v1/golden");
const golden = (prefix: string) => readdirSync(GOLDEN)
  .filter((f) => f.startsWith(`${prefix}.`) && f.endsWith(".json"))
  .map((f) => ({ name: f, body: JSON.parse(readFileSync(join(GOLDEN, f), "utf8")) as Record<string, unknown> }));

let answer: unknown;
beforeEach(() => {
  vi.stubEnv("VEXA_ADMIN_API_URL", "http://admin.test");
  vi.stubEnv("VEXA_INTERNAL_API_SECRET", "internal-secret");
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 })));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("signin.v1 on the terminal side", () => {
  it("has a golden for every admission reason, and admits on every one of them", async () => {
    const responses = golden("SigninAdmissionResponse");
    for (const why of ADMITTED_REASONS) expect(responses.map((g) => g.body.why)).toContain(why);
    for (const g of responses) {
      answer = g.body;
      const verdict = await signinAdmission("someone@example.com");
      expect(verdict.admitted, g.name).toBe(g.body.admitted);
      if (verdict.admitted) expect(verdict.why).toBe(g.body.why);
    }
  });

  it("refuses an answer whose reason the contract does not know, even one that says admitted", async () => {
    answer = { admitted: true, why: "unclaimed-instance" };
    expect(await signinAdmission("someone@example.com")).toEqual({ admitted: false, why: "not-allowed" });
  });

  it("reads every claim golden as the reason it carries", async () => {
    const responses = golden("AdminClaimResponse");
    for (const why of CLAIM_REASONS) expect(responses.map((g) => g.body.why)).toContain(why);
    for (const g of responses) {
      answer = g.body;
      const r = await claimAdminRole(11, "ABCD-EF01-JKMN-PQRS");
      expect(r.ok && r.claimed === g.body.claimed && r.why === g.body.why, g.name).toBe(true);
    }
  });
});
