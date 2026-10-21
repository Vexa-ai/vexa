import { describe, expect, it } from "vitest";
import { getDetailedStatus, getOutcomeInfo, isFailureOutcome } from "../src/types/vexa";

describe("meeting outcome labels", () => {
  it("shows a host that never admitted the bot as not admitted, not as a failure", () => {
    const data = { completion_reason: "join_failure", outcome_class: "host_or_platform" as const, outcome_reason: "not_admitted_1655" };
    const s = getDetailedStatus("failed", data);
    expect(s.label).toBe("Not admitted by host");
    expect(s.color).not.toContain("red");
    expect(isFailureOutcome("failed", data)).toBe(false);
  });

  it("labels each class", () => {
    const label = (outcome_class: "ours" | "host_or_platform" | "user" | "normal" | "unknown", outcome_reason: string) =>
      getOutcomeInfo({ outcome_class, outcome_reason })?.label;
    expect(label("host_or_platform", "rejected_by_host")).toBe("Host rejected the bot");
    expect(label("host_or_platform", "tenant_requires_sign_in")).toBe("Your organisation requires sign-in");
    expect(label("host_or_platform", "unsupported_url_zoom_webinar")).toBe("Link not supported");
    expect(label("user", "stopped_before_admission")).toBe("Stopped");
    expect(label("normal", "everyone_left")).toBe("Meeting ended");
    expect(label("ours", "join_automation")).toBe("Bot error");
    expect(label("unknown", "join_failure@joining")).toBe("Ended");
  });

  it("renders only ours as an error", () => {
    expect(getOutcomeInfo({ outcome_class: "ours", outcome_reason: "page_or_browser" })?.isFailure).toBe(true);
    expect(getDetailedStatus("failed", { outcome_class: "ours", outcome_reason: "page_or_browser" }).color).toContain("red");
  });

  it("falls back to the status-based label when no outcome is present", () => {
    expect(getOutcomeInfo({})).toBeNull();
    expect(getOutcomeInfo({ outcome_class: "pending" })).toBeNull();
    expect(getDetailedStatus("failed", {}).label).toBe("Failed");
    expect(isFailureOutcome("failed", {})).toBe(true);
  });
});
