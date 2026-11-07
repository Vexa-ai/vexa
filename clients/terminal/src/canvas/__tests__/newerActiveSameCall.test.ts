import { describe, expect, it } from "vitest";

import { newerActiveSameCall } from "../useMeeting";
import type { MeetingMock } from "../../surfaces/meetingModel";

/** F169 (ledger 2026-09-02 14:17Z-14:30Z) — "the panel stays on the ended meeting". When the
 *  person's session has a NEWER active meeting for the SAME call (a bot re-dropped after the first
 *  session ended: same `native_id`, new row `id`), the live-transcript panel must follow the
 *  newest active one instead of freezing on the row it was originally pinned to. */
function row(over: Partial<MeetingMock>): MeetingMock {
  return {
    id: "1", title: "t", when: "", status: "past", platform: "Google Meet",
    participants: [], mentioned: [], actions: [], transcript: [], insights: [],
    ...over,
  };
}

describe("newerActiveSameCall (F169 — follow the live one)", () => {
  it("follows a newer LIVE row for the same native_id when the pinned row already ended", () => {
    const ended = row({ id: "10", native_id: "abc-defg-hij", status: "past" });
    const redropped = row({ id: "11", native_id: "abc-defg-hij", status: "live" });
    const meetings = [ended, redropped];
    expect(newerActiveSameCall(meetings, ended)).toBe(redropped);
  });

  it("picks the NEWEST (highest row id) among several re-drops, not just any live match", () => {
    const ended = row({ id: "10", native_id: "abc-defg-hij", status: "past" });
    const first_redrop = row({ id: "11", native_id: "abc-defg-hij", status: "past" }); // also ended meanwhile
    const latest_redrop = row({ id: "12", native_id: "abc-defg-hij", status: "live" });
    const meetings = [ended, first_redrop, latest_redrop];
    expect(newerActiveSameCall(meetings, ended)).toBe(latest_redrop);
  });

  it("does nothing when the pinned row is itself still live", () => {
    const live = row({ id: "10", native_id: "abc-defg-hij", status: "live" });
    const other = row({ id: "11", native_id: "abc-defg-hij", status: "live" });
    expect(newerActiveSameCall([live, other], live)).toBeUndefined();
  });

  it("does nothing when nothing shares the native_id (a different call entirely)", () => {
    const ended = row({ id: "10", native_id: "abc-defg-hij", status: "past" });
    const unrelated = row({ id: "99", native_id: "zzz-zzzz-zzz", status: "live" });
    expect(newerActiveSameCall([ended, unrelated], ended)).toBeUndefined();
  });

  it("does nothing when the ended row has no native_id (an unresolved/mock placeholder)", () => {
    const ended = row({ id: "10", native_id: undefined, status: "past" });
    const live = row({ id: "11", native_id: "abc-defg-hij", status: "live" });
    expect(newerActiveSameCall([ended, live], ended)).toBeUndefined();
  });

  it("a genuinely finished call (no re-drop) stays put — no candidate at all", () => {
    const ended = row({ id: "10", native_id: "abc-defg-hij", status: "past" });
    expect(newerActiveSameCall([ended], ended)).toBeUndefined();
  });

  it("never follows itself back", () => {
    const ended = row({ id: "10", native_id: "abc-defg-hij", status: "past" });
    expect(newerActiveSameCall([ended], ended)).toBeUndefined();
  });
});
