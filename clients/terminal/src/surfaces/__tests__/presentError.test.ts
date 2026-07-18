/** The presenter seam (issue #533): `presentError` maps a typed `ApiError`'s STRUCTURED fields to
 *  user vocabulary; the untranslated plumbing string is always preserved on `detail` and echoed to
 *  the console (P18's observable channel). The fixture range is the C1 inventory: one hand-authored
 *  `ApiError` per failure class, each pinned to the user-truth headline it must show.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiError, presentError } from "../apiClient";

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
});

// C1 fixtures — the failure range, sanitized (localhost only, no real keys).
const FIX = {
  network: new ApiError(0, "Failed to fetch", "/api/user/calendar"),
  gatewayDown: new ApiError(502, "upstream unreachable: ConnectError", "/api/vexa/user/calendar"),
  upstreamTimeout: new ApiError(504, "upstream timeout", "/api/meetings"),
  auth: new ApiError(401, "Invalid API key", "/api/tokens"),
  scope: new ApiError(403, "Forbidden", "/api/admin/settings/models"),
  validation422Json: new ApiError(422, '{"field":"ics_url","msg":"invalid url"}', "/api/user/calendar"),
  rateLimit: new ApiError(429, "Bot concurrency limit reached", "/api/bots"),
  // A typed backend detail that SHOULD pass through verbatim (the STT 503 —
  // meeting-api bot_spawn/router.py: TranscriptionNotConfigured → 503 detail=str(e)).
  typed503: new ApiError(503, "Transcription is not configured: set TRANSCRIPTION_SERVICE_URL or ask your operator", "/api/bots"),
  emptyDetail: new ApiError(500, "", "/api/meetings"),
};

describe("presentError — ApiError → user truth (fixture range)", () => {
  it("network (status 0) → couldn't reach the server", () => {
    expect(presentError(FIX.network).headline).toBe("Couldn't reach the Vexa server — check that the stack is running.");
  });
  it("gateway-down 502 → backend-unreachable truth, NOT the plumbing string", () => {
    const p = presentError(FIX.gatewayDown);
    expect(p.headline).toBe("The Vexa server can't reach a backend service right now.");
    expect(p.headline).not.toContain("502");
    expect(p.headline).not.toContain("ConnectError");
    expect(p.headline).not.toContain("/api/");
  });
  it("upstream-timeout 504 → same backend-unreachable truth", () => {
    expect(presentError(FIX.upstreamTimeout).headline).toBe("The Vexa server can't reach a backend service right now.");
  });
  it("auth 401 → key rejected, sign in again", () => {
    expect(presentError(FIX.auth).headline).toBe("Your API key was rejected — sign in again.");
  });
  it("scope 403 → no access", () => {
    expect(presentError(FIX.scope).headline).toBe("Your key doesn't have access to this.");
  });
  it("validation 422 with a JSON payload detail → generic (payloads are operator-only)", () => {
    const p = presentError(FIX.validation422Json);
    expect(p.headline).toBe("The request failed (422).");
    expect(p.headline).not.toContain("{");
  });
  it("rate-limit 429 → rate limit truth", () => {
    expect(presentError(FIX.rateLimit).headline).toBe("Rate limit hit — try again in a moment.");
  });
  it("A3: a typed backend 503 detail passes through VERBATIM — never genericized", () => {
    expect(presentError(FIX.typed503).headline).toBe(FIX.typed503.detail);
  });
  it("non-JSON body (empty detail) → generic with the status", () => {
    expect(presentError(FIX.emptyDetail).headline).toBe("The request failed (500).");
  });
});

describe("presentError — loudness no-regression (A4, P18)", () => {
  it("the plumbing string is preserved intact on `detail` for every fixture", () => {
    for (const e of Object.values(FIX)) {
      expect(presentError(e).detail).toBe(e.message);
    }
  });
  it("the console channel carries the full plumbing string", () => {
    presentError(FIX.gatewayDown);
    expect(console.warn).toHaveBeenCalledWith("api failure", FIX.gatewayDown.message);
  });
  it("presentation never mutates the error: ApiError.status/detail/url/message unchanged", () => {
    const e = FIX.gatewayDown;
    presentError(e);
    expect(e.status).toBe(502);
    expect(e.detail).toBe("upstream unreachable: ConnectError");
    expect(e.url).toBe("/api/vexa/user/calendar");
    expect(e.message).toBe("/api/vexa/user/calendar → 502: upstream unreachable: ConnectError");
  });
});

describe("presentError — non-ApiError failures", () => {
  it("a fetch()-level TypeError reads as the network truth", () => {
    expect(presentError(new TypeError("Failed to fetch")).headline).toBe("Couldn't reach the Vexa server — check that the stack is running.");
  });
  it("a prose Error message (a client edge's own user-facing reason) passes through", () => {
    expect(presentError(new Error("That doesn't look like a valid ICS address.")).headline).toBe("That doesn't look like a valid ICS address.");
  });
  it("a payload-shaped Error message → generic", () => {
    const p = presentError(new Error('{"detail":"boom"}'));
    expect(p.headline).toBe("Something went wrong — details are in the browser console.");
    expect(p.detail).toBe('{"detail":"boom"}');
  });
  it("a non-Error throw → generic, stringified on detail", () => {
    const p = presentError("boom");
    expect(p.headline).toBe("Something went wrong — details are in the browser console.");
    expect(p.detail).toBe("boom");
  });
});
