import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VexaAPIError } from "../src/lib/api";
import {
  SERVICE_DENIAL_REASONS,
  serviceDenialFromError,
  serviceDenialFromResponseBody,
  serviceDenialPresentation,
} from "../src/lib/service-denial";

/** The wire shape `POST /bots` emits for a refused Join (FastAPI nests it). */
function denialError(reason: string, status = 403): VexaAPIError {
  return new VexaAPIError("API request failed: Forbidden", status, {
    detail: {
      code: "service_not_allowed",
      reason,
      decision_id: "dec_123",
    },
  });
}

describe("serviceDenialPresentation", () => {
  it("renders insufficient_balance as a paywall with one fixing action", () => {
    const view = serviceDenialPresentation("insufficient_balance");
    expect(view.kind).toBe("paywall");
    expect(view.title).toBe("Out of credit");
    expect(view.body).toContain("Your prepaid balance is empty.");
    expect(view.action).toEqual({ label: "Add funds", href: "/account?tab=bots" });
    expect(view.retryable).toBe(false);
  });

  it("names the balance when the caller knows it", () => {
    const view = serviceDenialPresentation("insufficient_balance", {
      balanceCents: 137,
      planLabel: "Pay-as-you-go",
    });
    expect(view.body).toContain("Your prepaid balance on Pay-as-you-go is $1.37.");
  });

  it("falls back to 'empty' rather than printing a nonsense balance", () => {
    for (const balanceCents of [null, undefined, -1, 1.5, Number.NaN]) {
      const view = serviceDenialPresentation("insufficient_balance", {
        balanceCents: balanceCents as number | null | undefined,
      });
      expect(view.body).toContain("Your prepaid balance is empty.");
    }
  });

  it("says billing_unavailable is ours, the account is fine, and retrying helps", () => {
    const view = serviceDenialPresentation("billing_unavailable");
    expect(view.kind).toBe("retryable");
    expect(view.title).toBe("Billing system temporarily unavailable");
    expect(view.body).toContain("Your account is fine");
    expect(view.body).toContain("Try again shortly.");
    expect(view.action).toBeNull();
    expect(view.retryable).toBe(true);
  });

  it("states the concurrency ceiling by number when known", () => {
    const view = serviceDenialPresentation("concurrency_limit_reached", {
      concurrencyCeiling: 3,
    });
    expect(view.kind).toBe("limit");
    expect(view.body).toContain("Your plan runs 3 bots at once");
    expect(view.retryable).toBe(true);
  });

  it("singularizes a ceiling of one", () => {
    const view = serviceDenialPresentation("concurrency_limit_reached", {
      concurrencyCeiling: 1,
    });
    expect(view.body).toContain("Your plan runs 1 bot at once");
  });

  it("describes the concurrency state when the ceiling is unknown", () => {
    const view = serviceDenialPresentation("concurrency_limit_reached");
    expect(view.body).toContain("Every bot your plan allows is already in a meeting.");
  });

  it("routes billing_setup_required, payment_past_due and spend_cap_reached to their own fixes", () => {
    expect(serviceDenialPresentation("billing_setup_required").action?.label).toBe(
      "Finish billing setup",
    );
    expect(serviceDenialPresentation("payment_past_due").action?.label).toBe(
      "Update payment method",
    );
    expect(serviceDenialPresentation("spend_cap_reached").action?.label).toBe(
      "Review spend cap",
    );
  });

  it("gives every known reason words that are not 'Access denied'", () => {
    for (const reason of SERVICE_DENIAL_REASONS) {
      const view = serviceDenialPresentation(reason);
      expect(view.title).not.toBe("Access denied");
      expect(view.body.length).toBeGreaterThan(0);
    }
  });
});

describe("unmapped reasons", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
    vi.unstubAllEnvs();
  });

  it("shows the reason code VERBATIM, never a bare 'Access denied'", () => {
    const view = serviceDenialPresentation("some_new_backend_reason");
    expect(view.kind).toBe("unknown");
    expect(view.body).toContain("service not allowed: some_new_backend_reason");
    expect(view.body).not.toContain("Access denied");
  });

  it("labels an empty reason rather than emitting a dangling colon", () => {
    const view = serviceDenialPresentation("   ");
    expect(view.body).toContain("service not allowed: unspecified");
  });

  it("fails loud outside production so a developer sees the gap", () => {
    vi.stubEnv("NODE_ENV", "development");
    serviceDenialPresentation("brand_new_reason");
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining("unmapped service-authority reason: brand_new_reason"),
    );
  });

  it("fails loud IN PRODUCTION too — that is the only place drift happens", () => {
    // An unmapped reason means the service authority shipped a reason ahead of
    // this module's copy, which can only be observed in a production build. A
    // dev-only console.error left the net with no signal at all.
    vi.stubEnv("NODE_ENV", "production");
    serviceDenialPresentation("brand_new_reason");
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining("unmapped service-authority reason: brand_new_reason"),
    );
  });
});

describe("serviceDenialFromResponseBody", () => {
  it("reads the FastAPI-nested denial POST /bots emits", () => {
    const view = serviceDenialFromResponseBody({
      detail: { code: "service_not_allowed", reason: "insufficient_balance" },
    });
    expect(view?.kind).toBe("paywall");
  });

  it("reads the bare denial the platform routes emit", () => {
    const view = serviceDenialFromResponseBody({
      code: "service_not_allowed",
      reason: "spend_cap_reached",
    });
    expect(view?.reason).toBe("spend_cap_reached");
  });

  it("maps the 503 service_authority_unavailable to the retryable outage copy", () => {
    const view = serviceDenialFromResponseBody({
      detail: {
        code: "service_authority_unavailable",
        reason: "service_authority_unavailable",
      },
    });
    expect(view?.kind).toBe("retryable");
    expect(view?.retryable).toBe(true);
  });

  it("returns null for anything that is not a denial", () => {
    expect(serviceDenialFromResponseBody(null)).toBeNull();
    expect(serviceDenialFromResponseBody("Invalid or missing API key")).toBeNull();
    expect(serviceDenialFromResponseBody({ detail: "Not authenticated" })).toBeNull();
    expect(serviceDenialFromResponseBody({ code: "something_else" })).toBeNull();
  });
});

describe("serviceDenialFromError", () => {
  it("extracts the denial from a thrown 403", () => {
    const view = serviceDenialFromError(denialError("insufficient_balance"));
    expect(view?.title).toBe("Out of credit");
  });

  it("extracts the denial from a 503 carrying the unavailable code", () => {
    const error = new VexaAPIError("API request failed", 503, {
      detail: { code: "service_authority_unavailable", reason: "service_authority_unavailable" },
    });
    expect(serviceDenialFromError(error)?.kind).toBe("retryable");
  });

  it("never claims a 401 is a denial — that stays an auth failure", () => {
    const error = new VexaAPIError("Not authenticated", 401, {
      detail: { code: "service_not_allowed", reason: "insufficient_balance" },
    });
    expect(serviceDenialFromError(error)).toBeNull();
  });

  it("leaves a genuine permission 403 alone", () => {
    const error = new VexaAPIError("Invalid or missing API key", 403, {
      detail: "Invalid or missing API key",
    });
    expect(serviceDenialFromError(error)).toBeNull();
  });

  it("ignores plain Errors", () => {
    expect(serviceDenialFromError(new Error("boom"))).toBeNull();
    expect(serviceDenialFromError(undefined)).toBeNull();
  });
});
