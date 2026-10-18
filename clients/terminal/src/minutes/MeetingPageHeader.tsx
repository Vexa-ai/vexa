"use client";
import { MeetingLanguageControl } from "../surfaces/TranscriptionLanguagePicker";
import { ExportMeetingButton, exportable } from "../surfaces/MeetingBundleActions";
import { CallName } from "./CallName";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";
import { splitLeadingH1 } from "./workspaceFrontPage";
import { type as ty } from "./tokens";
import { MeetingShareButton } from "../surfaces/MeetingShare";

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
  // ONE header row: the title, then platform · date · status muted beside it. Both have a zero
  // basis, so neither forces a wrap: the title grows first (to its full width, else it truncates
  // down to 4em) and the metadata takes what is left, truncating with the full text on hover. Only
  // a control that cannot fit even then wraps below, never under the header's icons.
  // A zero basis: in the wrapping document header this shares the first line with the icon group
  // instead of claiming the whole line and pushing the icons onto a row of their own. A large grow
  // factor: the header's spacer (also `1 1 0%`) would otherwise take half the free width.
  return <div style={{ flex: "100 1 0%", minWidth: 0 }}>
    <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: 8, rowGap: 4, minWidth: 0 }}>
      <div data-doc-name title={path} style={{ ...ty.title, fontSize: 13.5, flex: "3 1 0%", minWidth: "4em", maxWidth: "max-content", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}><CallName meeting={meeting} label={title} /></div>
      {meeting && <MeetingShareButton meeting={meeting} />}
      {metadata && <div data-meeting-metadata title={metadata} style={{ ...ty.meta, flex: "1 1 0%", minWidth: 0, color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{metadata}</div>}
    </div>
    {meeting && <MeetingLanguageControl key={meeting.id} meeting={meeting} />}
    {meeting && exportable(meeting) && <div style={{ marginTop: 8 }}><ExportMeetingButton meetingId={meeting.id} /></div>}
  </div>;
}
