/** The denial-reason vocabulary, pinned — TERMINAL twin.
 *
 *  `core/meetings/contracts/service-authority.v1` (this repo) is SEALED and types
 *  `Decision.reason` as an OPAQUE `{"type":"string","minLength":1}` — by design, since that
 *  contract is deliberately policy-free. Nothing on the wire constrains the set, and the set is now
 *  consumed by THREE copy modules across TWO repositories:
 *
 *    1. Vexa-ai/vexa           clients/terminal/src/surfaces/serviceDenial.ts        (this one)
 *    2. Vexa-ai/vexa           services/dashboard/src/lib/service-denial.ts
 *    3. Vexa-ai/vexa-platform  services/webapp/apps/webapp/lib/service-denial-view.ts
 *
 *  Three hand-maintained copies with nothing checking them against each other is the silent-drift
 *  class: a reason added in the authority renders here as a raw "service not allowed: <code>",
 *  which is the customer-visible failure that opened Vexa-ai/vexa-platform#291.
 *
 *  This file is the terminal's pin. Its twins — which carry the same literal — are
 *  `services/dashboard/tests/service-denial-vocabulary.test.ts` (this repo) and
 *  `services/webapp/apps/webapp/__tests__/service-authority-reason-vocabulary.test.ts` (platform),
 *  the latter additionally deriving the set from `ServiceAuthorityReason` and `decisionReason()` at
 *  their source. Change one, change all six places named below.
 *
 *  NOTE FOR THE MERGER: the two older pins' failure messages still name only FOUR places, because
 *  they predate this module. Reconcile all three message lists at merge time.
 *
 *  Narrowing `reason` to an enum in the contract is the deeper fix, and it is a BREAKING change to
 *  a frozen `.vN`: it needs a `service-authority.v2` on a `lane:contract` human-reviewed PR
 *  (gate:contract-version), not an edit to v1.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../apiClient";
import {
  SERVICE_DENIAL_REASONS,
  isAccessError,
  resolveJoinError,
  serviceDenialPresentation,
} from "../serviceDenial";

const PINNED_REASON_VOCABULARY = [
  "allowed",
  "billing_setup_required",
  "insufficient_balance",
  "payment_past_due",
  "spend_cap_reached",
  "concurrency_limit_reached",
  "billing_unavailable",
] as const;

const CROSS_REPO_FIXUP = [
  "The denial-reason vocabulary changed. Update ALL of:",
  "  1. this pin (Vexa-ai/vexa clients/terminal/src/surfaces/__tests__/serviceDenialVocabulary.test.ts)",
  "  2. Vexa-ai/vexa clients/terminal/src/surfaces/serviceDenial.ts (the terminal copy module)",
  "  3. Vexa-ai/vexa services/dashboard/src/lib/service-denial.ts (the dashboard copy module)",
  "  4. Vexa-ai/vexa services/dashboard/tests/service-denial-vocabulary.test.ts (the dashboard pin)",
  "  5. Vexa-ai/vexa-platform services/webapp/apps/webapp/lib/service-denial-view.ts (the webapp copy module)",
  "  6. Vexa-ai/vexa-platform services/webapp/apps/webapp/__tests__/service-authority-reason-vocabulary.test.ts (the webapp pin)",
  "A reason with copy on only one surface reaches customers as a raw code.",
].join("\n");

const sorted = (values: readonly string[]) => [...new Set(values)].sort();

const denial = (status: number, reason: string) =>
  new ApiError(status, `{"code":"service_not_allowed","reason":"${reason}"}`, "/api/bots", {
    detail: { code: "service_not_allowed", reason, decision_id: "d-1" },
  });

describe("service-authority denial-reason vocabulary (Vexa-ai/vexa-platform#291)", () => {
  it("maps exactly the pinned vocabulary — no more, no less", () => {
    expect(sorted(SERVICE_DENIAL_REASONS), CROSS_REPO_FIXUP).toEqual(
      sorted(PINNED_REASON_VOCABULARY),
    );
  });

  it("gives every pinned reason real copy, never the verbatim fallback", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      for (const reason of PINNED_REASON_VOCABULARY) {
        const view = serviceDenialPresentation(reason);
        expect(view.body, `${reason}\n${CROSS_REPO_FIXUP}`).not.toContain(
          `service not allowed: ${reason}. If this keeps happening`,
        );
        expect(view.body).not.toMatch(/access denied/i);
        expect(view.title.length).toBeGreaterThan(0);
      }
      // Not one of them fell through to the unmapped branch.
      expect(consoleError, CROSS_REPO_FIXUP).not.toHaveBeenCalled();
    } finally {
      consoleError.mockRestore();
    }
  });

  it("keeps 'allowed' out of the denial-styled kinds", () => {
    // `allowed` is in the vocabulary because the authority emits it, but it is not a denial: a
    // caller reaching the view with it has mismatched allow/reason and must not be shown a paywall.
    expect(serviceDenialPresentation("allowed").kind).toBe("unknown");
    expect(serviceDenialPresentation("allowed").action).toBeNull();
  });

  it("says the same sentence the dashboard says, word for word", () => {
    // The byte-alignment claim in serviceDenial.ts's header, made checkable for the two lines a
    // customer is most likely to screenshot at us.
    expect(serviceDenialPresentation("insufficient_balance")).toMatchObject({
      kind: "paywall",
      title: "Out of credit",
      body:
        "Your prepaid balance is empty. Add funds and the bot joins immediately — " +
        "nothing else about your account needs changing.",
      action: { label: "Add funds", href: "/account?tab=bots" },
    });
    expect(serviceDenialPresentation("billing_unavailable")).toMatchObject({
      kind: "retryable",
      title: "Billing system temporarily unavailable",
      body:
        "Your account is fine — we could not reach the billing ledger just now. " +
        "Try again shortly.",
      retryable: true,
    });
  });

  it("puts the caller's numbers in the copy when it has them", () => {
    expect(serviceDenialPresentation("insufficient_balance", {
      balanceCents: 250, planLabel: "Pay-as-you-go",
    }).body).toContain("Your prepaid balance on Pay-as-you-go is $2.50.");
    expect(serviceDenialPresentation("concurrency_limit_reached", {
      concurrencyCeiling: 1,
    }).body).toContain("Your plan runs 1 bot at once");
    expect(serviceDenialPresentation("concurrency_limit_reached", {
      concurrencyCeiling: 3,
    }).body).toContain("Your plan runs 3 bots at once");
  });
});

describe("access failures are never dressed as a paywall", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;
  let consoleWarn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
    consoleWarn.mockRestore();
  });

  it("renders a 401 as an access error, not a denial panel", () => {
    const error = new ApiError(401, "Invalid API token", "/api/bots", { detail: "Invalid API token" });
    expect(resolveJoinError(error).kind).toBe("message");
    expect(isAccessError(error)).toBe(true);
  });

  it("renders a genuine permission 403 as an access error, not a paywall", () => {
    // A real permission fault: 403, but no `service_not_allowed` code in the body. Routing this to
    // the paywall would tell a customer to pay for a problem money cannot fix.
    const error = new ApiError(403, "Not authorized to access this meeting", "/api/bots", {
      detail: "Not authorized to access this meeting",
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("message");
    if (state.kind !== "message") throw new Error("unreachable");
    expect(state.headline).not.toMatch(/credit|balance|billing|payment/i);
    expect(isAccessError(error)).toBe(true);
  });

  it("does not confuse a 401 that happens to carry a denial-shaped body", () => {
    // Defence in depth: the 401 branch short-circuits before body sniffing, so an expired token can
    // never be presented as a billing problem.
    const error = denial(401, "insufficient_balance");
    expect(resolveJoinError(error).kind).toBe("message");
    expect(isAccessError(error)).toBe(true);
  });

  it("still routes a real 403 denial to the paywall panel", () => {
    // The positive control for the three negatives above, and the regression this PR exists for:
    // before it, this error rendered as "Your key doesn't have access to this."
    const error = denial(403, "insufficient_balance");
    const state = resolveJoinError(error);
    expect(state.kind).toBe("denial");
    if (state.kind !== "denial") throw new Error("unreachable");
    expect(state.presentation.kind).toBe("paywall");
    expect(state.presentation.title).toBe("Out of credit");
    expect(isAccessError(error)).toBe(false);
  });

  it("routes the 503 authority outage to a retryable panel, not a paywall", () => {
    const error = new ApiError(503, "", "/api/bots", {
      detail: { code: "service_authority_unavailable", reason: "service_authority_unavailable" },
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("denial");
    if (state.kind !== "denial") throw new Error("unreachable");
    expect(state.presentation.kind).toBe("retryable");
    expect(state.presentation.retryable).toBe(true);
    expect(state.presentation.action).toBeNull();
  });

  it("recovers a denial whose body only survived as the flattened detail string", () => {
    // `ApiError.detail` is the operator string. A call site built before `ApiError.body` existed
    // still produces a transparent panel rather than "Your key doesn't have access to this."
    const error = new ApiError(
      403,
      '{"code":"service_not_allowed","reason":"payment_past_due"}',
      "/api/bots",
    );
    const state = resolveJoinError(error);
    expect(state.kind).toBe("denial");
    if (state.kind !== "denial") throw new Error("unreachable");
    expect(state.presentation.title).toBe("Payment past due");
  });

  it("logs an unmapped reason in a production build so the net can see it", () => {
    vi.stubEnv("NODE_ENV", "production");
    try {
      const state = resolveJoinError(denial(403, "region_unsupported"));
      expect(state.kind).toBe("denial");
      if (state.kind !== "denial") throw new Error("unreachable");
      // Verbatim in front of the customer — never flattened to "Access denied".
      expect(state.presentation.kind).toBe("unknown");
      expect(state.presentation.body).toContain("service not allowed: region_unsupported");
      expect(consoleError).toHaveBeenCalledWith(
        expect.stringContaining("unmapped service-authority reason: region_unsupported"),
      );
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
