import { describe, it, expect } from "vitest";
import { meetingHeader } from "../MeetingPageHeader";
import type { MeetingMock } from "../../surfaces/meetingModel";
const meeting = { title: "Design review", platform: "Google Meet", start_time: "2026-10-08T13:40:00Z", live_status: "active", participants: [{ name: "A" }, { name: "B" }] } as MeetingMock;
describe("meeting document header", () => {
  it("uses the actual meeting title over a generic document heading", () => {
    const h = meetingHeader("# Google Meet meeting", meeting, Date.parse("2026-10-08T14:05:30Z"));
    expect(h.title).toBe("Design review");
    expect(h.metadata).toContain("Google Meet");
    expect(h.metadata).toContain("2026");
    // a live meeting says how long it has been running; people, not "participants"
    expect(h.metadata).toContain("2 people · live · 25 min");
  });
  it("preserves a descriptive page title and honors an explicit meeting rename", () => {
    expect(meetingHeader("# Architecture decisions", meeting).title).toBe("Architecture decisions");
    expect(meetingHeader("# Architecture decisions", { ...meeting, title_custom: "Weekly review" }).title).toBe("Weekly review");
  });
  it("a finished meeting's line is just platform · when · who — no status word", () => {
    const done = meetingHeader("", { ...meeting, live_status: "completed", status: "past" } as MeetingMock);
    expect(done.metadata).toMatch(/2 people$/);
    expect(done.metadata).not.toMatch(/completed|past|live/);
    expect(meetingHeader("", { ...meeting, participants: [{ name: "A" }] } as MeetingMock, Date.parse("2026-10-08T13:40:00Z")).metadata)
      .toContain("1 person · live · 0 min");
  });
  it("does not invent metadata for an unresolved meeting or render invalid dates", () => {
    expect(meetingHeader("---\ntype: meeting\n---\n# Planning")).toEqual({ title: "Planning", metadata: "" });
    expect(meetingHeader("", { ...meeting, start_time: "bad" }).metadata).not.toContain("Invalid");
  });
});
