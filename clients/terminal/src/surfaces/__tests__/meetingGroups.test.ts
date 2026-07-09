/** meeting ≠ run grouping (design-spec meeting-lifecycle-v2 §v4, BUG-1 fix): rows sharing a
 *  calendar UID / native link collapse into ONE meeting whose `current` run carries the state. */
import { describe, expect, it } from "vitest";
import type { MeetingMock } from "../meetingModel";
import { groupMeetings, meetingGroupKey } from "../meetingGroups";

const m = (over: Partial<MeetingMock>): MeetingMock => ({
  id: over.id ?? Math.random().toString(36).slice(2),
  title: "t", when: "w", status: "past", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [],
  ...over,
});

describe("meetingGroupKey", () => {
  it("prefers calendar uid, then platform+native, then the row id", () => {
    expect(meetingGroupKey(m({ id: "1", calendar_uid: "u1", native_id: "abc" }))).toBe("cal:u1");
    expect(meetingGroupKey(m({ id: "1", native_id: "abc" }))).toBe("native:Google Meet:abc");
    expect(meetingGroupKey(m({ id: "1" }))).toBe("row:1");
  });
});

describe("groupMeetings", () => {
  it("BUG-1: a scheduled meeting's finished sibling runs do NOT surface as separate meetings", () => {
    const scheduled = m({ id: "S", live_status: "scheduled", calendar_uid: "u1", scheduled_at: "2026-07-10T10:00:00Z" });
    const oldRun = m({ id: "R1", live_status: "completed", calendar_uid: "u1", start_time: "2026-07-01T10:00:00Z", has_recording: true });
    const olderRun = m({ id: "R2", live_status: "completed", calendar_uid: "u1", start_time: "2026-06-24T10:00:00Z", has_recording: true });
    const [g, ...rest] = groupMeetings([scheduled, oldRun, olderRun]);
    expect(rest).toEqual([]);                       // ONE meeting, not three rows
    expect(g.phase).toBe("prep");                   // the upcoming occurrence carries the state
    expect(g.current.id).toBe("S");
    expect(g.pastRuns.map((r) => r.id)).toEqual(["R1", "R2"]);  // history, newest first
  });

  it("a live run wins the group even against an upcoming plan", () => {
    const plan = m({ id: "P", live_status: "scheduled", native_id: "abc", scheduled_at: "2026-07-15T10:00:00Z" });
    const live = m({ id: "L", live_status: "active", status: "live", native_id: "abc", start_time: "2026-07-08T10:00:00Z" });
    const [g] = groupMeetings([plan, live]);
    expect(g.phase).toBe("live");
    expect(g.current.id).toBe("L");
    expect(g.runs.map((r) => r.id)).toEqual(["L", "P"]);
  });

  it("only-finished runs pick the newest as current", () => {
    const a = m({ id: "A", live_status: "completed", native_id: "abc", start_time: "2026-07-01T10:00:00Z" });
    const b = m({ id: "B", live_status: "completed", native_id: "abc", start_time: "2026-07-08T10:00:00Z" });
    const [g] = groupMeetings([a, b]);
    expect(g.phase).toBe("post");
    expect(g.current.id).toBe("B");
  });

  it("distinct identities never merge; link-less rows are their own meeting", () => {
    const linkless = m({ id: "1", live_status: "idle" });
    const linkless2 = m({ id: "2", live_status: "idle" });
    const linked = m({ id: "3", live_status: "completed", native_id: "abc" });
    expect(groupMeetings([linkless, linkless2, linked])).toHaveLength(3);
  });

  it("two plans on one identity: the SOONEST is the meeting's next occurrence", () => {
    const later = m({ id: "later", live_status: "scheduled", calendar_uid: "u", scheduled_at: "2026-07-20T10:00:00Z" });
    const sooner = m({ id: "sooner", live_status: "scheduled", calendar_uid: "u", scheduled_at: "2026-07-10T10:00:00Z" });
    const [g] = groupMeetings([later, sooner]);
    expect(g.current.id).toBe("sooner");
  });
});
