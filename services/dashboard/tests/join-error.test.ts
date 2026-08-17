import { describe, expect, it } from "vitest";
import { VexaAPIError } from "../src/lib/api";
import {
  denialActionUrl,
  isAccessError,
  resolveJoinError,
} from "../src/lib/join-error";

function apiError(status: number, message: string, details?: unknown) {
  return new VexaAPIError(message, status, details);
}

function denial(reason: string, status = 403) {
  return apiError(status, "API request failed: Forbidden", {
    detail: { code: "service_not_allowed", reason, decision_id: "dec_1" },
  });
}

describe("resolveJoinError — the join flow's rendering states", () => {
  it("renders a paywall PANEL, not a toast, when the account is out of credit", () => {
    const state = resolveJoinError(denial("insufficient_balance"));
    expect(state.kind).toBe("denial");
    if (state.kind !== "denial") throw new Error("unreachable");
    expect(state.presentation.kind).toBe("paywall");
    expect(state.presentation.title).toBe("Out of credit");
    expect(state.presentation.action?.href).toBe("/account?tab=bots");
  });

  it("renders billing_unavailable as a retryable panel with no CTA", () => {
    const state = resolveJoinError(denial("billing_unavailable"));
    if (state.kind !== "denial") throw new Error("expected a denial panel");
    expect(state.presentation.retryable).toBe(true);
    expect(state.presentation.action).toBeNull();
  });

  it("renders an unmapped reason verbatim in the panel", () => {
    const state = resolveJoinError(denial("gravity_exceeded"));
    if (state.kind !== "denial") throw new Error("expected a denial panel");
    expect(state.presentation.body).toContain("service not allowed: gravity_exceeded");
  });

  it("hands a missing Zoom connection to the OAuth flow, not to a panel", () => {
    const error = apiError(400, "Zoom OAuth connection is missing");
    const state = resolveJoinError(error, {
      platform: "zoom",
      canStartZoomOAuth: true,
    });
    expect(state.kind).toBe("zoom-oauth");
  });

  it("does not attempt the OAuth hand-off when it cannot run", () => {
    const error = apiError(400, "Zoom OAuth connection is missing");
    const state = resolveJoinError(error, {
      platform: "zoom",
      canStartZoomOAuth: false,
    });
    expect(state.kind).toBe("toast");
  });

  it("keeps a genuine 403 permission fault as an access error toast", () => {
    const state = resolveJoinError(
      apiError(403, "Invalid or missing API key", { detail: "Invalid or missing API key" }),
    );
    expect(state).toEqual({
      kind: "toast",
      title: "Access denied",
      description: "Invalid or missing API key",
    });
  });

  it("keeps a 401 as an authentication failure toast", () => {
    const state = resolveJoinError(apiError(401, "Not authenticated"));
    if (state.kind !== "toast") throw new Error("expected a toast");
    expect(state.title).toBe("Authentication failed");
  });

  it("never turns a service denial into 'Access denied'", () => {
    for (const reason of [
      "insufficient_balance",
      "billing_unavailable",
      "concurrency_limit_reached",
      "payment_past_due",
      "spend_cap_reached",
      "billing_setup_required",
      "who_knows",
    ]) {
      const state = resolveJoinError(denial(reason));
      if (state.kind !== "denial") throw new Error(`${reason} did not render a panel`);
      expect(state.presentation.title).not.toBe("Access denied");
    }
  });

  it("falls back to a toast for transport failures", () => {
    const state = resolveJoinError(new Error("network request failed"));
    if (state.kind !== "toast") throw new Error("expected a toast");
    expect(state.title).toBe("Connection error");
  });

  it("survives a non-Error rejection", () => {
    const state = resolveJoinError("something odd");
    expect(state.kind).toBe("toast");
  });
});

describe("denialActionUrl", () => {
  it("resolves the account path against the webapp origin", () => {
    expect(denialActionUrl("https://vexa.ai", "/account?tab=bots")).toBe(
      "https://vexa.ai/account?tab=bots",
    );
  });

  it("does not double the slash on a trailing-slash base", () => {
    expect(denialActionUrl("https://vexa.ai/", "/account?tab=balance")).toBe(
      "https://vexa.ai/account?tab=balance",
    );
  });
});

describe("isAccessError", () => {
  it("is true for 401 and for a genuine 403", () => {
    expect(isAccessError(apiError(401, "Not authenticated"))).toBe(true);
    expect(isAccessError(apiError(403, "Invalid or missing API key"))).toBe(true);
  });

  it("is false for a service denial dressed as a 403", () => {
    expect(isAccessError(denial("insufficient_balance"))).toBe(false);
  });

  it("is false for everything else", () => {
    expect(isAccessError(apiError(500, "boom"))).toBe(false);
    expect(isAccessError(new Error("boom"))).toBe(false);
  });
});
