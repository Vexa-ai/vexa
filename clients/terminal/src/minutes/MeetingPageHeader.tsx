"use client";
import { MeetingControls } from "./MeetingControls";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";
import { splitLeadingH1 } from "./workspaceFrontPage";
import { type as ty } from "./tokens";

/** What the header's one metadata line says about where the meeting is: `live · N min` while the
 *  bot is in the room, the bot's own word while it is getting there or leaving, and nothing once the
 *  meeting is over — a finished meeting's line is just platform · when · who. */
export function meetingStatus(meeting: MeetingMock | undefined, now: number = Date.now()): string {
  if (!meeting) return "";
  const raw = meeting.live_status || (meeting.status === "live" ? "active" : "");
  if (!raw || ["completed", "stopped", "past"].includes(raw)) return "";
  if (raw === "active") {
    const start = Date.parse(meeting.start_time ?? "");
    const mins = Number.isFinite(start) ? Math.max(0, Math.floor((now - start) / 60000)) : NaN;
    return Number.isFinite(mins) ? `live · ${mins} min` : "live";
  }
  return raw.replaceAll("_", " ");
}

export function meetingHeader(body: string, meeting?: MeetingMock, now: number = Date.now()) {
  const heading = splitLeadingH1(body).title;
  const generic = !heading || /^(google meet|zoom|microsoft teams|teams|live)?\s*meeting$/i.test(heading);
  const title = meeting?.title_custom || (!generic ? heading : meeting?.title) || heading || "Meeting";
  const when = meeting?.start_time || meeting?.scheduled_at;
  const date = when ? new Date(when) : null;
  const status = meetingStatus(meeting, now);
  const people = meeting?.participants.length ?? 0;
  const metadata = [
    meeting?.platform,
    date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
    }).format(date) : undefined,
    people ? `${people} ${people === 1 ? "person" : "people"}` : undefined,
    status || undefined,
  ].filter(Boolean).join(" · ");
  return { title, metadata };
}

export function MeetingPageHeader({ meetingId, body, path }: { meetingId: string; body: string; path: string }) {
  const meetings = useLiveMeetings();
  // Bind to the document's meeting, never the currently selected chat or another live call.
  const meeting = meetings.find(m => m.id === meetingId || m.native_id === meetingId);
  const { title, metadata } = meetingHeader(body, meeting);
  return <div style={{ flex: "1 1 auto", minWidth: 0 }}>
    <div data-doc-name title={path} style={{ ...ty.title, fontSize: 13.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
    {metadata && <div data-meeting-metadata style={{ ...ty.meta, marginTop: 3, color: "var(--t3)", overflowWrap: "anywhere" }}>{metadata}</div>}
    <MeetingControls meetingId={meetingId} />
  </div>;
}
