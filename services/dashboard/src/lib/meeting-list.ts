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

export function prepareMeetingPage(
  rawMeetings: RawMeeting[],
  hasMore: boolean,
): Pick<InitialMeetingsPage, "meetings" | "hasMore"> {
  const meetings = rawMeetings
    .map(mapRawMeeting)
    .filter((meeting) => !isHiddenDeletedMeeting(meeting));
  meetings.sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );
  return { meetings, hasMore };
}
