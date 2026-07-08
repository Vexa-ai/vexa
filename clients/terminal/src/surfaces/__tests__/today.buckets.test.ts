/** Today-tab v4 flow-queue zones + the shared meeting-phase source (design-spec §v4, mockup §08b). */
import { describe, expect, it } from "vitest";
import { meetingPhase, type MeetingMock } from "../meetingModel";
import { groupMeetings } from "../meetingGroups";
import { endOfWeek, todayZones } from "../today";

const m = (over: Partial<MeetingMock>): MeetingMock => ({
  id: over.id ?? Math.random().toString(36).slice(2),
  title: "t", when: "w", status: "past", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [],
  ...over,
});

// Wed 2026-07-08 (local) — the coming Sunday is 2026-07-12
const NOW = new Date("2026-07-08T12:00:00");
const zones = (rows: MeetingMock[], reviewed: Set<string> = new Set()) =>
  todayZones(groupMeetings(rows), NOW, reviewed);

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

describe("endOfWeek", () => {
  it("ends the week on the coming Sunday (today, when today IS Sunday)", () => {
    expect(endOfWeek(new Date("2026-07-08T12:00:00")).getDay()).toBe(0);
    expect(endOfWeek(new Date("2026-07-08T12:00:00")).getDate()).toBe(12);
    expect(endOfWeek(new Date("2026-07-12T12:00:00")).getDate()).toBe(12);  // Sunday stays
  });
});

describe("todayZones", () => {
  it("splits now / next / this week / later / to review", () => {
    const live = m({ id: "L", live_status: "active", status: "live", native_id: "lll" });
    const next = m({ id: "N", live_status: "scheduled", scheduled_at: "2026-07-09T10:00:00Z", native_id: "nnn" });
    const week = m({ id: "W", live_status: "scheduled", scheduled_at: "2026-07-11T10:00:00Z", native_id: "www" });
    const later = m({ id: "X", live_status: "scheduled", scheduled_at: "2026-07-15T10:00:00Z", native_id: "xxx" });
    const done = m({ id: "D", live_status: "completed", has_recording: true, start_time: "2026-07-08T09:00:00Z", native_id: "ddd" });
    const z = zones([done, later, week, next, live]);
    expect(z.now.map((g) => g.current.id)).toEqual(["L"]);
    expect(z.next?.current.id).toBe("N");
    expect(z.thisWeek.map((g) => g.current.id)).toEqual(["W"]);
    expect(z.later.map((g) => g.current.id)).toEqual(["X"]);
    expect(z.toReview.map((g) => g.pastRuns[0].id)).toEqual(["D"]);
  });

  it("BUG-1: a future synced meeting with old runs shows ONCE as upcoming, never under review-as-duplicate", () => {
    const scheduled = m({ id: "S", live_status: "scheduled", calendar_uid: "u1", scheduled_at: "2026-07-09T10:00:00Z" });
    const oldRun = m({ id: "R", live_status: "completed", calendar_uid: "u1", start_time: "2026-07-01T10:00:00Z", has_recording: true });
    const z = zones([scheduled, oldRun]);
    expect(z.next?.current.id).toBe("S");
    // the finished run is still owed a review — via the SAME group, not a duplicate meeting
    expect(z.toReview.map((g) => g.key)).toEqual(["cal:u1"]);
    expect(z.toReview[0].pastRuns[0].id).toBe("R");
  });

  it("an unscheduled plan stays visible in this week (it needs attention), after timed ones", () => {
    const timed = m({ id: "T", live_status: "scheduled", scheduled_at: "2026-07-09T10:00:00Z", native_id: "ttt" });
    const timed2 = m({ id: "T2", live_status: "scheduled", scheduled_at: "2026-07-10T10:00:00Z", native_id: "t2" });
    const noTime = m({ id: "U", live_status: "idle" });
    const z = zones([noTime, timed2, timed]);
    expect(z.next?.current.id).toBe("T");
    expect(z.thisWeek.map((g) => g.current.id)).toEqual(["T2", "U"]);
  });

  it("a reviewed recap leaves TO REVIEW; unreviewable empty runs never enter", () => {
    const done = m({ id: "D", live_status: "completed", has_recording: true, start_time: "2026-07-08T09:00:00Z", native_id: "d" });
    const emptyFailed = m({ id: "F", live_status: "failed" });   // nothing captured
    expect(zones([done, emptyFailed]).toReview.map((g) => g.pastRuns[0].id)).toEqual(["D"]);
    expect(zones([done, emptyFailed], new Set(["D"])).toReview).toEqual([]);
  });

  it("to review is newest-first and capped at 8", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      m({ id: `r${i}`, live_status: "completed", native_id: `n${i}`, has_recording: true,
        start_time: `2026-07-0${(i % 7) + 1}T0${i % 9}:00:00Z` }));
    const z = zones(rows);
    expect(z.toReview.length).toBe(8);
    const times = z.toReview.map((g) => g.pastRuns[0].start_time!);
    expect([...times].sort().reverse()).toEqual(times);
  });

  it("a stopped run with a transcript still reaches to review (stopped ≠ discarded)", () => {
    const stopped = m({ id: "S", live_status: "stopped", start_time: "2026-07-08T10:00:00Z", native_id: "s" });
    expect(zones([stopped]).toReview.map((g) => g.pastRuns[0].id)).toEqual(["S"]);
  });
});
