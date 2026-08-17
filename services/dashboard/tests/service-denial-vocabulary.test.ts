import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VexaAPIError } from "../src/lib/api";
import { isAccessError, resolveJoinError } from "../src/lib/join-error";
import {
  SERVICE_DENIAL_REASONS,
  serviceDenialPresentation,
} from "../src/lib/service-denial";

/**
 * The denial-reason vocabulary, pinned.
 *
 * `core/meetings/contracts/service-authority.v1` (this repo) is SEALED and
 * types `Decision.reason` as an OPAQUE `{"type":"string","minLength":1}` — by
 * design, since that contract is deliberately policy-free. Nothing on the wire
 * constrains the set, and the set is consumed by TWO copy modules in TWO
 * repositories:
 *
 *   1. Vexa-ai/vexa           services/dashboard/src/lib/service-denial.ts
 *   2. Vexa-ai/vexa-platform  services/webapp/apps/webapp/lib/service-denial-view.ts
 *
 * Two hand-maintained copies with nothing checking them against each other is
 * the silent-drift class: a reason added in the authority renders here as a raw
 * "service not allowed: <code>", which is the customer-visible failure that
 * opened Vexa-ai/vexa-platform#291.
 *
 * This list is the pin. The twin assertion — which additionally derives the set
 * from `ServiceAuthorityReason` and `decisionReason()` at their source — lives
 * at Vexa-ai/vexa-platform
 * services/webapp/apps/webapp/__tests__/service-authority-reason-vocabulary.test.ts
 * and carries the same literal. Change one, change all four places named below.
 *
 * Narrowing `reason` to an enum in the contract is the deeper fix, and it is a
 * BREAKING change to a frozen `.vN`: it needs a `service-authority.v2` on a
 * `lane:contract` human-reviewed PR (gate:contract-version), not an edit to v1.
 */
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
  "  1. this pin (Vexa-ai/vexa services/dashboard/tests/service-denial-vocabulary.test.ts)",
  "  2. Vexa-ai/vexa services/dashboard/src/lib/service-denial.ts (the copy module)",
  "  3. Vexa-ai/vexa-platform services/webapp/apps/webapp/lib/service-denial-view.ts (the twin copy module)",
  "  4. Vexa-ai/vexa-platform services/webapp/apps/webapp/__tests__/service-authority-reason-vocabulary.test.ts (the twin pin)",
  "A reason with copy on only one surface reaches customers as a raw code.",
].join("\n");

const sorted = (values: readonly string[]) => [...new Set(values)].sort();

describe("service-authority denial-reason vocabulary (Vexa-ai/vexa-platform#291)", () => {
  it("maps exactly the pinned vocabulary — no more, no less", () => {
    expect(sorted(SERVICE_DENIAL_REASONS), CROSS_REPO_FIXUP).toEqual(
      sorted(PINNED_REASON_VOCABULARY),
    );
  });

  it("gives every pinned reason real copy, never the verbatim fallback", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
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
    // `allowed` is in the vocabulary because the authority emits it, but it is
    // not a denial: a caller reaching the view with it has mismatched
    // allow/reason and must not be shown a paywall.
    expect(serviceDenialPresentation("allowed").kind).toBe("unknown");
    expect(serviceDenialPresentation("allowed").action).toBeNull();
  });
});

describe("access failures are never dressed as a paywall", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
    vi.unstubAllEnvs();
  });

  it("renders a 401 as an access error, not a denial panel", () => {
    const error = new VexaAPIError("API request failed: Unauthorized", 401, {
      detail: "Invalid API token",
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("toast");
    expect(isAccessError(error)).toBe(true);
  });

  it("renders a genuine permission 403 as an access error, not a paywall", () => {
    // A real permission fault: 403, but no `service_not_allowed` code in the
    // body. Routing this to the paywall would tell a customer to pay for a
    // problem money cannot fix.
    const error = new VexaAPIError("API request failed: Forbidden", 403, {
      detail: "Not authorized to access this meeting",
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("toast");
    if (state.kind !== "toast") throw new Error("unreachable");
    expect(state.title).not.toMatch(/credit|balance|billing|payment/i);
    expect(isAccessError(error)).toBe(true);
  });

  it("does not confuse a 401 that happens to carry a denial-shaped body", () => {
    // Defence in depth: the 401 branch short-circuits before body sniffing, so
    // an expired token can never be presented as a billing problem.
    const error = new VexaAPIError("API request failed: Unauthorized", 401, {
      detail: { code: "service_not_allowed", reason: "insufficient_balance" },
    });
    expect(resolveJoinError(error).kind).toBe("toast");
    expect(isAccessError(error)).toBe(true);
  });

  it("still routes a real 403 denial to the paywall panel", () => {
    // The positive control for the three negatives above.
    const error = new VexaAPIError("API request failed: Forbidden", 403, {
      detail: { code: "service_not_allowed", reason: "insufficient_balance" },
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("denial");
    if (state.kind !== "denial") throw new Error("unreachable");
    expect(state.presentation.kind).toBe("paywall");
    expect(isAccessError(error)).toBe(false);
  });

  it("logs an unmapped reason in a production build so the net can see it", () => {
    vi.stubEnv("NODE_ENV", "production");
    const error = new VexaAPIError("API request failed: Forbidden", 403, {
      detail: { code: "service_not_allowed", reason: "region_unsupported" },
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("denial");
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining("unmapped service-authority reason: region_unsupported"),
    );
  });
});
