/** Today-tab bucketing + the shared meeting-phase source (design-spec meeting-lifecycle-v2, W2/W3). */
import { describe, expect, it } from "vitest";
import { meetingPhase, type MeetingMock } from "../meetingModel";
import { todayBuckets } from "../today";

const m = (over: Partial<MeetingMock>): MeetingMock => ({
  id: over.id ?? Math.random().toString(36).slice(2),
  title: "t", when: "w", status: "past", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [],
  ...over,
});

describe("meetingPhase", () => {
  it("maps the raw lifecycle onto prep/live/post", () => {
    expect(meetingPhase(m({ live_status: "idle" }))).toBe("prep");
    expect(meetingPhase(m({ live_status: "scheduled" }))).toBe("prep");
    for (const s of ["requested", "joining", "awaiting_admission", "active", "needs_help", "stopping"])
      expect(meetingPhase(m({ live_status: s, status: "live" }))).toBe("live");
    for (const s of ["completed", "failed", "stopped"])
      expect(meetingPhase(m({ live_status: s }))).toBe("post");
  });
  it("falls back to the coarse bucket when no raw status rides the mock", () => {
    expect(meetingPhase(m({ status: "live" }))).toBe("live");
    expect(meetingPhase(m({ status: "past" }))).toBe("post");
  });
});

describe("todayBuckets", () => {
  it("splits live / planned / recorded and drops empty terminal rows", () => {
    const live = m({ id: "L", live_status: "active", status: "live" });
    const joining = m({ id: "J", live_status: "joining", status: "live" });
    const planned = m({ id: "P", live_status: "scheduled", scheduled_at: "2026-07-13T10:00:00Z" });
    const recorded = m({ id: "R", live_status: "completed", has_recording: true, start_time: "2026-07-08T12:00:00Z" });
    const emptyFailed = m({ id: "F", live_status: "failed" });  // nothing captured, nothing to show
    const b = todayBuckets([recorded, emptyFailed, planned, live, joining]);
    expect(b.now.map((x) => x.id)).toEqual(["L", "J"]);
    expect(b.upcoming.map((x) => x.id)).toEqual(["P"]);
    expect(b.recent.map((x) => x.id)).toEqual(["R"]);
  });

  it("orders upcoming by scheduled time with unscheduled plans last", () => {
    const later = m({ id: "later", live_status: "scheduled", scheduled_at: "2026-07-14T10:00:00Z" });
    const sooner = m({ id: "sooner", live_status: "scheduled", scheduled_at: "2026-07-13T09:00:00Z" });
    const noTime = m({ id: "noTime", live_status: "idle" });
    const b = todayBuckets([later, noTime, sooner]);
    expect(b.upcoming.map((x) => x.id)).toEqual(["sooner", "later", "noTime"]);
  });

  it("orders recent newest-first and caps the list", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      m({ id: `r${i}`, live_status: "completed", has_recording: true, start_time: `2026-07-0${(i % 7) + 1}T0${i % 9}:00:00Z` }));
    const b = todayBuckets(rows);
    expect(b.recent.length).toBe(8);
    const times = b.recent.map((x) => x.start_time!);
    expect([...times].sort().reverse()).toEqual(times);
  });

  it("a stopped run with a transcript still reaches recent (stopped ≠ discarded)", () => {
    const stopped = m({ id: "S", live_status: "stopped", start_time: "2026-07-08T10:00:00Z" });
    expect(todayBuckets([stopped]).recent.map((x) => x.id)).toEqual(["S"]);
  });
});
