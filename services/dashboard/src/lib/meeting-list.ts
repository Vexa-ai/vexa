import type { Meeting, Platform } from "@/types/vexa";

export const MEETINGS_PAGE_SIZE = 50;

export interface RawMeeting {
  id: number;
  user_id?: number;
  platform: Platform;
  native_meeting_id: string;
  constructed_meeting_url?: string;
  status: string;
  start_time: string | null;
  end_time: string | null;
  bot_container_id: string | null;
  data: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

export type InitialMeetingsPage = {
  state: "ready" | "subscription_required" | "fallback";
  meetings: Meeting[];
  hasMore: boolean;
};

export function mapRawMeeting(raw: RawMeeting): Meeting {
  return {
    id: raw.id.toString(),
    platform: raw.platform,
    platform_specific_id: raw.native_meeting_id,
    status: raw.status as Meeting["status"],
    start_time: raw.start_time,
    end_time: raw.end_time,
    bot_container_id: raw.bot_container_id,
    data: raw.data as Meeting["data"],
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
}

export function isHiddenDeletedMeeting(meeting: Meeting): boolean {
  return meeting.data?.redacted === true || !meeting.platform_specific_id;
}

// #1222: the list orders by MEETING EVENT time with non-terminal rows pinned first — the exact
// key the server emits (meeting-api list_meetings: status-pin DESC, COALESCE(data.scheduled_at,
// start_time, created_at) DESC, id DESC). A calendar-managed row is created at IMPORT time, so
// the old created_at sort buried a meeting that was live right now under every row created since
// the import. The client sort must match or pagination merges re-shuffle the server's pages.
const PINNED_STATUSES = new Set<Meeting["status"]>([
  "scheduled", "requested", "joining", "awaiting_admission", "active", "stopping",
]);

function eventTime(meeting: Meeting): number {
  const scheduled = typeof meeting.data?.scheduled_at === "string"
    ? Date.parse(meeting.data.scheduled_at) : NaN;
  if (!Number.isNaN(scheduled)) return scheduled;
  const start = meeting.start_time ? Date.parse(meeting.start_time) : NaN;
  if (!Number.isNaN(start)) return start;
  const created = Date.parse(meeting.created_at);
  return Number.isNaN(created) ? 0 : created;
}

export function compareMeetingsListOrder(a: Meeting, b: Meeting): number {
  const pin = Number(PINNED_STATUSES.has(b.status)) - Number(PINNED_STATUSES.has(a.status));
  if (pin !== 0) return pin;
  const time = eventTime(b) - eventTime(a);
  if (time !== 0) return time;
  return (Number(b.id) || 0) - (Number(a.id) || 0);
}

export function prepareMeetingPage(
  rawMeetings: RawMeeting[],
  hasMore: boolean,
): Pick<InitialMeetingsPage, "meetings" | "hasMore"> {
  const meetings = rawMeetings
    .map(mapRawMeeting)
    .filter((meeting) => !isHiddenDeletedMeeting(meeting));
  meetings.sort(compareMeetingsListOrder);
  return { meetings, hasMore };
}
