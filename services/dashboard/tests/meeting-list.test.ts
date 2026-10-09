import { describe, expect, it } from "vitest";
import { prepareMeetingPage } from "../src/lib/meeting-list";

describe("meeting list preparation", () => {
  it("maps, sorts, and suppresses deleted shells before server rendering", () => {
    const page = prepareMeetingPage(
      [
        {
          id: 1,
          platform: "teams",
          native_meeting_id: "older",
          status: "completed",
          start_time: null,
          end_time: null,
          bot_container_id: null,
          data: {},
          created_at: "2026-08-14T10:00:00Z",
        },
        {
          id: 2,
          platform: "google_meet",
          native_meeting_id: "newer",
          status: "completed",
          start_time: null,
          end_time: null,
          bot_container_id: null,
          data: {},
          created_at: "2026-08-14T11:00:00Z",
        },
        {
          id: 3,
          platform: "zoom",
          native_meeting_id: "",
          status: "completed",
          start_time: null,
          end_time: null,
          bot_container_id: null,
          data: { redacted: true },
          created_at: "2026-08-14T12:00:00Z",
        },
      ],
      true,
    );

    expect(page.hasMore).toBe(true);
    expect(page.meetings.map((meeting) => meeting.id)).toEqual(["2", "1"]);
  });
});
