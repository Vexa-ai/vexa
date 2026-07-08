/** Today-tab v5 agenda timeline + the shared meeting-phase source
 *  (design-spec today-v5-agenda-timeline; supersedes the v4 zones tests). */
import { describe, expect, it } from "vitest";
import { meetingPhase, type MeetingMock } from "../meetingModel";
import { groupMeetings } from "../meetingGroups";
import { agendaWindow, pastFeed, deviationPhrase } from "../today";

const m = (over: Partial<MeetingMock>): MeetingMock => ({
  id: over.id ?? Math.random().toString(36).slice(2),
  title: "t", when: "w", status: "past", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [],
  ...over,
});

// Wed 2026-07-08 (local)
const NOW = new Date("2026-07-08T12:00:00");
const window0 = (rows: MeetingMock[], offset = 0) => agendaWindow(groupMeetings(rows), NOW, offset);

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

describe("agendaWindow", () => {
  it("groups upcoming meetings by day inside the 7-day window; today always renders", () => {
    const thu = m({ id: "A", live_status: "scheduled", scheduled_at: "2026-07-09T10:00:00", native_id: "aaa" });
    const thu2 = m({ id: "B", live_status: "scheduled", scheduled_at: "2026-07-09T15:00:00", native_id: "bbb" });
    const mon = m({ id: "C", live_status: "scheduled", scheduled_at: "2026-07-13T09:00:00", native_id: "ccc" });
    const beyond = m({ id: "X", live_status: "scheduled", scheduled_at: "2026-07-20T09:00:00", native_id: "xxx" });
    const days = window0([mon, thu2, thu, beyond]);
    expect(days.map((d) => d.key)).toEqual(["2026-07-08", "2026-07-09", "2026-07-13"]);
    expect(days[0].groups).toEqual([]);                                    // today: "No events today"
    expect(days[1].groups.map((g) => g.current.id)).toEqual(["A", "B"]);   // time-sorted
    expect(days[2].groups.map((g) => g.current.id)).toEqual(["C"]);
  });

  it("pages weeks: the beyond-Sunday meeting appears at offset 1, today's rows don't", () => {
    const near = m({ id: "A", live_status: "scheduled", scheduled_at: "2026-07-09T10:00:00", native_id: "aaa" });
    const far = m({ id: "X", live_status: "scheduled", scheduled_at: "2026-07-20T09:00:00", native_id: "xxx" });
    const days = window0([near, far], 1);
    expect(days.map((d) => d.key)).toEqual(["2026-07-20"]);
    expect(days[0].groups.map((g) => g.current.id)).toEqual(["X"]);
  });

  it("a live meeting shows IN PLACE — and a stale schedule clamps onto today so it can't vanish", () => {
    const live = m({ id: "L", live_status: "active", status: "live", native_id: "lll", scheduled_at: "2026-07-07T10:00:00" });
    const days = window0([live]);
    expect(days.map((d) => d.key)).toEqual(["2026-07-08"]);
    expect(days[0].groups.map((g) => g.current.id)).toEqual(["L"]);
  });

  it("unscheduled plans attach to today on the current week only", () => {
    const loose = m({ id: "U", live_status: "idle", native_id: "uuu" });
    expect(window0([loose])[0].groups.map((g) => g.current.id)).toEqual(["U"]);
    expect(window0([loose], 1)).toEqual([]);
  });

  it("BUG-1 carry-over: a future synced meeting with old runs shows ONCE as upcoming", () => {
    const scheduled = m({ id: "S", live_status: "scheduled", calendar_uid: "u1", scheduled_at: "2026-07-09T10:00:00" });
    const oldRun = m({ id: "R", live_status: "completed", calendar_uid: "u1", start_time: "2026-07-01T10:00:00Z", has_recording: true });
    const days = window0([scheduled, oldRun]);
    const all = days.flatMap((d) => d.groups.map((g) => g.current.id));
    expect(all).toEqual(["S"]);
  });
});

describe("pastFeed", () => {
  it("newest finished run per meeting, newest first, day-grouped, capped", () => {
    const a = m({ id: "A", live_status: "completed", start_time: "2026-07-08T09:00:00Z", has_recording: true, native_id: "aaa" });
    const b = m({ id: "B", live_status: "completed", start_time: "2026-07-07T09:00:00Z", has_recording: true, native_id: "bbb" });
    const days = pastFeed(groupMeetings([b, a]));
    expect(days.map((d) => d.entries.map((e) => e.run.id))).toEqual([["A"], ["B"]]);
  });
  it("cap trims oldest", () => {
    const rows = Array.from({ length: 5 }, (_, i) =>
      m({ id: `P${i}`, live_status: "completed", start_time: `2026-07-0${i + 1}T09:00:00Z`, has_recording: true, native_id: `n${i}` }));
    const days = pastFeed(groupMeetings(rows), 2);
    expect(days.flatMap((d) => d.entries.map((e) => e.run.id))).toEqual(["P4", "P3"]);
  });
});

describe("deviationPhrase (the one-phrase law)", () => {
  const g = (rows: MeetingMock[]) => groupMeetings(rows)[0];
  it("live wins", () => {
    expect(deviationPhrase(g([m({ live_status: "active", status: "live", native_id: "x" })]))?.tone).toBe("live");
  });
  it("import failure is loud", () => {
    expect(deviationPhrase(g([m({ live_status: "scheduled", auto_join_error: "no link" })]))?.tone).toBe("danger");
  });
  it("link-less plan is loud", () => {
    expect(deviationPhrase(g([m({ live_status: "scheduled" })]))?.tone).toBe("danger");
  });
  it("unbound workspace nudges toward the brief", () => {
    expect(deviationPhrase(g([m({ live_status: "scheduled", native_id: "x" })]))?.text).toBe("no brief yet");
  });
  it("a prepared meeting is QUIET — no phrase", () => {
    expect(deviationPhrase(g([m({ live_status: "scheduled", native_id: "x", workspace_id: "oenb" })]))).toBeNull();
  });
});
