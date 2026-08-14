import { describe, expect, it } from "vitest";
import { groupMeetingsByCalendar, meetingParticipantCount } from "../src/lib/calendar-view";
import type { CalendarConnection } from "../src/lib/calendar-api";
import type { Meeting } from "../src/types/vexa";

const calendars: CalendarConnection[] = [
  { id: "work", name: "Work", ics_url_set: true, ics_url_masked: "…", auto_join: true, enabled: true, bot_name: "Work bot" },
  { id: "personal", name: "Personal", ics_url_set: true, ics_url_masked: "…", auto_join: true, enabled: true, bot_name: "Personal bot" },
];

function meeting(id: string, at: string, sourceIds: string[]): Meeting {
  return {
    id,
    platform: "google_meet",
    platform_specific_id: id,
    status: "requested",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    created_at: at,
    data: {
      scheduled_at: at,
      attendees: [{ email: "one@example.com" }, { email: "two@example.com" }],
      calendar_sources: sourceIds.map((sourceId) => ({
        id: sourceId,
        name: sourceId === "work" ? "Work" : "Personal",
        uid: `${sourceId}:${id}`,
        auto_join: true,
      })),
    },
  };
}

describe("calendar schedule view", () => {
  it("groups and sorts upcoming meetings under each calendar source", () => {
    const shared = meeting("shared", "2026-08-14T16:00:00Z", ["work", "personal"]);
    const earlier = meeting("earlier", "2026-08-14T15:00:00Z", ["work"]);
    const groups = groupMeetingsByCalendar(calendars, [shared, earlier]);

    expect(groups.map((group) => [group.id, group.botName])).toEqual([
      ["work", "Work bot"],
      ["personal", "Personal bot"],
    ]);
    expect(groups[0].meetings.map((item) => item.id)).toEqual(["earlier", "shared"]);
    expect(groups[1].meetings.map((item) => item.id)).toEqual(["shared"]);
  });

  it("does not duplicate a meeting when one source is repeated", () => {
    const repeated = meeting("same", "2026-08-14T15:00:00Z", ["work", "work"]);
    expect(groupMeetingsByCalendar(calendars, [repeated])[0].meetings).toHaveLength(1);
    expect(meetingParticipantCount(repeated)).toBe(2);
  });
});
