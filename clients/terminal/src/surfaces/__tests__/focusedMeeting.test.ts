import { describe, expect, it } from "vitest";

import { focusedMeeting } from "../chat";
import type { MeetingMock } from "../meetingModel";

/** The chat's focus row is the row the transcript pane shows. A bot re-sent into the same call opens a
 *  new row with the same native id; a tab still naming the old row reads the new one's live transcript,
 *  so the turn must carry the new row's id too. */
function row(over: Partial<MeetingMock>): MeetingMock {
  return {
    id: "1", title: "t", when: "", status: "past", platform: "Google Meet",
    participants: [], mentioned: [], actions: [], transcript: [], insights: [],
    ...over,
  };
}

describe("focusedMeeting", () => {
  const ended = row({ id: "10", native_id: "abc-defg-hij", status: "past" });
  const live = row({ id: "11", native_id: "abc-defg-hij", status: "live" });

  it("follows the newer live row for the same native id, by row id or by native id", () => {
    expect(focusedMeeting([ended, live], "10")?.id).toBe("11");
    expect(focusedMeeting([live, ended], "abc-defg-hij")?.id).toBe("11");
  });

  it("keeps a row with no newer live session, and names nothing for an unknown ref", () => {
    expect(focusedMeeting([ended], "10")?.id).toBe("10");
    expect(focusedMeeting([ended, live], "nope")).toBeUndefined();
  });
});
