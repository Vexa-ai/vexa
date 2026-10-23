"use client";
/** A PAGE THAT BELONGS TO A MEETING NAMES IT — "Meeting: AIT planning call · open" — and opens the
 *  meeting's own view on a click (founder, UI lab 2026-10-14: the meeting is the sidebar with its
 *  note embedded, never the page embedding the meeting). */
import { OPEN_PAGE_EVENT } from "../platform";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { meetingName } from "./MeetingViewHeader";

export function MeetingChip({ meetingId }: { meetingId: string }) {
  const meetings = useLiveMeetings();
  const row = meetings.find((m) => String(m.id) === meetingId || m.native_id === meetingId);
  const name = row ? meetingName(row) : "this meeting";
  const open = () => window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail: { path: `meeting:${row?.id ?? meetingId}` } }));
  return (
    <button type="button" data-meeting-chip={meetingId} className="vx-meeting-chip" onClick={open}
      title={`Open the meeting: ${name}`}>
      <span className="vx-meeting-chip-k">Meeting:</span> <span className="vx-meeting-chip-name">{name}</span>
      <span aria-hidden> · </span><span className="vx-meeting-chip-open">open</span>
    </button>
  );
}
