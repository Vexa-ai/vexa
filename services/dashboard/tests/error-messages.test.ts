import { describe, expect, it } from "vitest";
import { VexaAPIError } from "../src/lib/api";
import { getUserFriendlyError } from "../src/lib/error-messages";

describe("getUserFriendlyError", () => {
  it("does not flatten a billing denial into 'Access denied'", () => {
    const error = new VexaAPIError("API request failed: Forbidden", 403, {
      detail: { code: "service_not_allowed", reason: "insufficient_balance" },
    });
    const { title, description } = getUserFriendlyError(error);
    expect(title).toBe("Out of credit");
    expect(description).toContain("Add funds");
  });

  it("does not flatten an unmapped denial reason either", () => {
    const error = new VexaAPIError("API request failed: Forbidden", 403, {
      detail: { code: "service_not_allowed", reason: "quota_frobnicated" },
    });
    expect(getUserFriendlyError(error).description).toContain(
      "service not allowed: quota_frobnicated",
    );
  });

  it("keeps a genuine 403 as an access error", () => {
    const error = new VexaAPIError("Invalid or missing API key", 403, {
      detail: "Invalid or missing API key",
    });
    expect(getUserFriendlyError(error)).toEqual({
      title: "Access denied",
      description: "Invalid or missing API key",
    });
  });

  it("keeps 401 as an authentication failure", () => {
    const error = new VexaAPIError("Not authenticated", 401);
    expect(getUserFriendlyError(error).title).toBe("Authentication failed");
  });

  it("states the concurrency ceiling the 0.10 core named", () => {
    const error = new VexaAPIError("Concurrent bot limit reached (2/3)", 403);
    const { title, description } = getUserFriendlyError(error);
    expect(title).toBe("Bot limit reached");
    expect(description).toContain("Your plan runs 3 bots at once and 2 are already in a meeting.");
  });

  it("states the ceiling from the bare-limit phrasing too", () => {
    const error = new VexaAPIError(
      "User has reached the maximum concurrent bot limit (1).",
      403,
    );
    expect(getUserFriendlyError(error).description).toContain(
      "Your plan runs 1 bot at once",
    );
  });

  it("still describes the state when no number is on the wire", () => {
    const error = new VexaAPIError("Concurrent bot limit reached", 403);
    expect(getUserFriendlyError(error).description).toContain(
      "maximum number of concurrent bots",
    );
  });

  it("keeps rate limits, server errors and network faults as they were", () => {
    expect(getUserFriendlyError(new Error("rate limit exceeded")).title).toBe(
      "Too many requests",
    );
    expect(getUserFriendlyError(new VexaAPIError("boom", 500)).title).toBe("Server error");
    expect(getUserFriendlyError(new Error("failed to fetch")).title).toBe(
      "Connection error",
    );
  });
});
