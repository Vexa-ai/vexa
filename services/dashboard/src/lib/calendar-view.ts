import type { Meeting } from "@/types/vexa";
import type { CalendarConnection } from "@/lib/calendar-api";

export interface CalendarMeetingGroup {
  id: string;
  name: string;
  botName: string;
  meetings: Meeting[];
}

export function isUpcomingAutoJoin(meeting: Meeting, nowMs: number): boolean {
  const at = typeof meeting.data.scheduled_at === "string"
    ? Date.parse(meeting.data.scheduled_at)
    : Number.NaN;
  return Number.isFinite(at)
    && at >= nowMs
    && meeting.data.auto_join !== false
    && meeting.platform !== "unknown"
    && Boolean(meeting.platform_specific_id)
    && Array.isArray(meeting.data.calendar_sources)
    && meeting.data.calendar_sources.some((source) => source.auto_join !== false);
}

export function groupMeetingsByCalendar(
  calendars: CalendarConnection[],
  meetings: Meeting[],
): CalendarMeetingGroup[] {
  const groups = new Map<string, CalendarMeetingGroup>();

  for (const calendar of calendars) {
    groups.set(calendar.id, {
      id: calendar.id,
      name: calendar.name,
      botName: calendar.bot_name || "Vexa",
      meetings: [],
    });
  }

  for (const meeting of meetings) {
    const seen = new Set<string>();
    for (const source of meeting.data.calendar_sources ?? []) {
      if (seen.has(source.id)) continue;
      seen.add(source.id);
      const group = groups.get(source.id) ?? {
        id: source.id,
        name: source.name,
        botName: source.bot_name || "Vexa",
        meetings: [],
      };
      group.meetings.push(meeting);
      groups.set(source.id, group);
    }
  }

  return Array.from(groups.values())
    .filter((group) => group.meetings.length > 0)
    .map((group) => ({
      ...group,
      meetings: group.meetings.toSorted(
        (a, b) => Date.parse(String(a.data.scheduled_at)) - Date.parse(String(b.data.scheduled_at)),
      ),
    }));
}

export function meetingParticipantCount(meeting: Meeting): number {
  if (Array.isArray(meeting.data.attendees)) return meeting.data.attendees.length;
  if (Array.isArray(meeting.data.participants)) return meeting.data.participants.length;
  return 0;
}
