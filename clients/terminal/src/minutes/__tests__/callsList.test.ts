/** `./callsList` — the pure half of the navigator's Calls view. */
import { describe, it, expect } from "vitest";
import { callLabel, callRows, contentMatches, groupCalls, matchCalls, callsPerWorkspace } from "../callsList";
import type { MeetingMock } from "../../surfaces/meetingModel";

const m = (o: Partial<MeetingMock> & { id: string }): MeetingMock => ({
  title: "", when: "", status: "past", live_status: "completed", platform: "Google Meet",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [], ...o,
});

describe("callLabel", () => {
  it("title, else platform + native id, else platform + row id", () => {
    expect(callLabel(m({ id: "1", title_custom: " Sync " }))).toBe("Sync");
    expect(callLabel(m({ id: "1", platform: "zoom", native_id: "123" }))).toBe("Zoom · 123");
    expect(callLabel(m({ id: "7", platform: "teams" }))).toBe("Teams · #7");
  });
});

describe("callRows", () => {
  it("drops plans and duplicates; live first, then newest", () => {
    const rows = callRows([
      m({ id: "1", start_time: "2026-10-01T00:00:00Z" }),
      m({ id: "2", start_time: "2026-10-03T00:00:00Z" }),
      m({ id: "2", start_time: "2026-10-03T00:00:00Z" }),
      m({ id: "3", live_status: "idle" }),
      m({ id: "4", live_status: "awaiting_admission", start_time: "2026-09-01T00:00:00Z" }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["4", "2", "1"]);
    expect(rows[0].live).toBe(true);
  });
});

describe("groupCalls", () => {
  const now = new Date("2026-10-11T12:00:00");
  it("by day, with Today / Yesterday", () => {
    const rows = callRows([
      m({ id: "1", start_time: new Date("2026-10-11T09:00:00").toISOString() }),
      m({ id: "2", start_time: new Date("2026-10-10T09:00:00").toISOString() }),
      m({ id: "3", start_time: new Date("2026-10-02T09:00:00").toISOString() }),
    ]);
    expect(groupCalls(rows, now).map((g) => g.label).slice(0, 2)).toEqual(["Today", "Yesterday"]);
    expect(groupCalls(rows, now)).toHaveLength(3);
  });
  it("by month once the list is long", () => {
    const rows = callRows(Array.from({ length: 41 }, (_, i) => m({ id: String(i + 1), start_time: new Date(2026, 9 - Math.floor(i / 20), 5).toISOString() })));
    expect(groupCalls(rows, now).every((g) => g.key.startsWith("m:"))).toBe(true);
  });
});

describe("matchCalls / contentMatches / callsPerWorkspace", () => {
  const rows = callRows([m({ id: "1", title_custom: "Acme review", workspace_id: "acme" }), m({ id: "2", title_custom: "Board" })]);
  it("title substring, case-insensitive; blank matches nothing", () => {
    expect(matchCalls("ACME", rows).map((r) => r.id)).toEqual(["1"]);
    expect(matchCalls("  ", rows)).toEqual([]);
  });
  it("content hits are kept to the listed calls, one per call", () => {
    const out = contentMatches([{ meeting_db_id: 999, snippet: "x" }, { meeting_db_id: 1, snippet: "a" }, { meeting_db_id: "1", snippet: "b" }], rows);
    expect(out.map((h) => [h.row.id, h.snippet])).toEqual([["1", "a"]]);
  });
  it("counts calls per visible workspace only", () => {
    expect([...callsPerWorkspace(rows, [{ key: "acme" }, { key: "desk" }])]).toEqual([["acme", 1]]);
  });
});

import { pageForArtifact } from "../roomView";
describe("pageForArtifact names a call it is told the name of", () => {
  it("label wins; absent → Transcript, as before", () => {
    expect(pageForArtifact({ path: "meeting:11", label: "Acme review" })).toEqual({ kind: "meeting", path: "11", label: "Acme review" });
    expect(pageForArtifact({ path: "meeting:11" })?.label).toBe("Transcript");
  });
});

describe("callRows · deleted calls and calls that never started", () => {
  it("never lists a call whose transcript and recordings were deleted (pending or completed)", () => {
    const rows = callRows([
      m({ id: "1", start_time: "2026-10-10T21:09:00Z", artifacts_deleted: true }),
      m({ id: "2", start_time: "2026-10-10T21:12:00Z" }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["2"]);
  });

  it("a call whose bot never got in is dated by its request, not filed under No date", () => {
    const now = new Date("2026-10-11T12:00:00");
    const rows = callRows([
      m({ id: "1", start_time: new Date("2026-10-11T09:00:00").toISOString() }),
      m({ id: "2", live_status: "failed", title_custom: "Campbell", created_at: new Date("2026-10-11T10:00:00").toISOString() }),
    ]);
    expect(rows.map((r) => r.id)).toEqual(["2", "1"]);
    expect(rows[0]).toMatchObject({ started: false });
    expect(rows[1]).toMatchObject({ started: true });
    expect(groupCalls(rows, now).map((g) => g.label)).toEqual(["Today"]);
  });
});

import { pageLabel } from "../callsList";
describe("pageLabel", () => {
  const meetings = [m({ id: "11", title_custom: "Renamed" })];
  it("a call's tab follows the meeting's current name; other tabs keep theirs", () => {
    expect(pageLabel({ kind: "meeting", path: "11", label: "Old" }, meetings)).toBe("Renamed");
    expect(pageLabel({ kind: "meeting", path: "12", label: "Old" }, meetings)).toBe("Old");
    expect(pageLabel({ path: "README.md", label: "README" }, meetings)).toBe("README");
  });
});
