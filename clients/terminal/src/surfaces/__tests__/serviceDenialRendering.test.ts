/** The terminal has NO refusal vocabulary — the pin, inverted.
 *
 *  This file used to pin a seven-value reason list against two sibling copy modules, because the
 *  terminal authored customer words for each reason it knew and rendered a raw code for each it did
 *  not. `core/meetings/contracts/service-authority.v1` types `Decision.reason` as an OPAQUE
 *  `{"type":"string","minLength":1}` and always did: nothing on the wire ever constrained the set,
 *  so every list of it was a guess that went stale in front of a customer
 *  (Vexa-ai/vexa-platform#291).
 *
 *  Every refusal now carries `message` and `action_url` authored by the deciding service
 *  (Vexa-ai/vexa#1532), so there is nothing left for this client to know. What is pinned instead is
 *  the ABSENCE: that the rendering is a pure function of the body, that a reason no build has ever
 *  seen renders exactly as well as a familiar one, and that no billing vocabulary, customer copy or
 *  account origin has crept back into the OSS module.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { ApiError } from "../apiClient";
import {
  isAccessError,
  resolveJoinError,
  serviceDenialFromError,
  serviceDenialFromResponseBody,
} from "../serviceDenial";

const denial = (
  status: number,
  fields: Record<string, unknown>,
  { structured = true }: { structured?: boolean } = {},
) => {
  const detail = { code: "service_not_allowed", decision_id: "d-1", ...fields };
  return new ApiError(
    status,
    JSON.stringify(detail),
    "/api/bots",
    structured ? { detail } : undefined,
  );
};

const presentationOf = (error: unknown) => {
  const state = resolveJoinError(error);
  if (state.kind !== "denial") throw new Error(`expected a denial, got ${state.kind}`);
  return state.presentation;
};

describe("a refusal renders in the decider's own words", () => {
  it("puts `<reason>: <message>` on line 1 and `HTTP <status> <code>` on line 2", () => {
    const view = presentationOf(
      denial(403, { reason: "insufficient_balance", message: "Top up to send bots." }),
    );
    expect(view.headline).toBe("insufficient_balance: Top up to send bots.");
    expect(view.detail).toBe("HTTP 403 service_not_allowed");
  });

  it("shows the action_url verbatim — no label, no path, no origin of ours", () => {
    const view = presentationOf(
      denial(403, {
        reason: "insufficient_balance",
        message: "Top up to send bots.",
        action_url: "https://billing.example.invalid/account?tab=balance",
      }),
    );
    expect(view.actionUrl).toBe("https://billing.example.invalid/account?tab=balance");
  });

  it("renders a reason this build has never heard of exactly as well as a familiar one", () => {
    // The whole point: no list, so there is no such thing as unmapped.
    const known = presentationOf(denial(403, { reason: "payment_past_due", message: "Fix the card." }));
    const alien = presentationOf(denial(403, { reason: "quantum_flux_exceeded", message: "Fix the card." }));
    expect(alien.headline).toBe("quantum_flux_exceeded: Fix the card.");
    expect(Object.keys(alien).sort()).toEqual(Object.keys(known).sort());
    expect(alien.detail).toBe(known.detail);
  });

  it("falls back to the bare reason when the decider authored no message", () => {
    const view = presentationOf(denial(403, { reason: "spend_cap_reached" }));
    expect(view.headline).toBe("spend_cap_reached");
    expect(view.detail).toBe("HTTP 403 service_not_allowed");
    expect(view.actionUrl).toBeNull();
  });

  it("falls back to `HTTP <status> <code>` when the decider said neither", () => {
    const view = presentationOf(denial(403, {}));
    expect(view.headline).toBe("HTTP 403 service_not_allowed");
  });

  it("uses the message alone when there is no reason to prefix it with", () => {
    const view = presentationOf(denial(403, { message: "This account cannot send bots." }));
    expect(view.headline).toBe("This account cannot send bots.");
  });

  it("carries the 503 authority outage through with its own status and code", () => {
    const error = new ApiError(503, "", "/api/bots", {
      detail: { code: "service_authority_unavailable", reason: "service_authority_unavailable" },
    });
    const view = presentationOf(error);
    expect(view.code).toBe("service_authority_unavailable");
    expect(view.detail).toBe("HTTP 503 service_authority_unavailable");
  });

  it("peels nested {detail:…} envelopes but never a body with siblings", () => {
    const nested = { detail: { detail: { code: "service_not_allowed", reason: "r", message: "m" } } };
    expect(serviceDenialFromResponseBody(nested, 403)?.headline).toBe("r: m");
    // A bare payload (the platform routes' shape) is read as-is.
    expect(
      serviceDenialFromResponseBody({ code: "service_not_allowed", reason: "r" }, 403)?.headline,
    ).toBe("r");
    // Siblings alongside `detail` are not an envelope: nothing is truncated, and this is not a denial.
    expect(serviceDenialFromResponseBody({ detail: "nope", extra: 1 }, 403)).toBeNull();
  });

  it("recovers a refusal whose body only survived as the flattened detail string", () => {
    // `ApiError.detail` is the operator string. A call site built before `ApiError.body` existed
    // still produces a transparent panel rather than "Your key doesn't have access to this."
    const view = presentationOf(
      denial(403, { reason: "payment_past_due", message: "Fix the card." }, { structured: false }),
    );
    expect(view.headline).toBe("payment_past_due: Fix the card.");
  });
});

describe("the OSS module carries no billing vocabulary", () => {
  // Read from the package root (vitest's cwd) — the test runs transformed, so `import.meta.url`
  // is not a file URL here.
  const read = (name: string) =>
    readFileSync(resolve(process.cwd(), "src/surfaces", name), "utf8");
  const source = read("serviceDenial.ts");
  const panel = read("ServiceDenialPanel.tsx");

  it("names no hosted origin — the terminal is self-hostable and the decider names its own URL", () => {
    for (const text of [source, panel]) expect(text).not.toMatch(/vexa\.ai/);
  });

  it("holds no customer copy and no reason list", () => {
    // Reasons appear in THIS file as fixtures; they must not appear in the module that renders them.
    const banned = [
      "Add funds",
      "Finish billing",
      "prepaid",
      "Out of credit",
      "insufficient_balance",
      "billing_setup_required",
      "payment_past_due",
      "spend_cap_reached",
      "concurrency_limit_reached",
      "billing_unavailable",
    ];
    for (const needle of banned) {
      expect(source, `serviceDenial.ts must not mention "${needle}"`).not.toContain(needle);
      expect(panel, `ServiceDenialPanel.tsx must not mention "${needle}"`).not.toContain(needle);
    }
  });
});

describe("access failures are never dressed as a service refusal", () => {
  let consoleWarn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleWarn.mockRestore();
  });

  it("renders a 401 as an access error, not a denial panel", () => {
    const error = new ApiError(401, "Invalid API token", "/api/bots", { detail: "Invalid API token" });
    expect(resolveJoinError(error).kind).toBe("message");
    expect(isAccessError(error)).toBe(true);
  });

  it("renders a genuine permission 403 as an access error", () => {
    // A real permission fault: 403, but no decision code in the body. Routing this to the refusal
    // panel would show the customer words the deciding service never said.
    const error = new ApiError(403, "Not authorized to access this meeting", "/api/bots", {
      detail: "Not authorized to access this meeting",
    });
    const state = resolveJoinError(error);
    expect(state.kind).toBe("message");
    if (state.kind !== "message") throw new Error("unreachable");
    expect(state.headline).toBe("Your key doesn't have access to this.");
    expect(isAccessError(error)).toBe(true);
  });

  it("does not confuse a 401 that happens to carry a denial-shaped body", () => {
    // Defence in depth: the 401 branch short-circuits before body sniffing, so an expired token can
    // never be presented as a service decision.
    const error = denial(401, { reason: "insufficient_balance", message: "Top up." });
    expect(resolveJoinError(error).kind).toBe("message");
    expect(serviceDenialFromError(error)).toBeNull();
    expect(isAccessError(error)).toBe(true);
  });

  it("still routes a real 403 decision to the panel", () => {
    // The positive control for the three negatives above: before any of this, the error below
    // rendered as "Your key doesn't have access to this."
    const error = denial(403, { reason: "insufficient_balance", message: "Top up." });
    expect(resolveJoinError(error).kind).toBe("denial");
    expect(isAccessError(error)).toBe(false);
  });
});
