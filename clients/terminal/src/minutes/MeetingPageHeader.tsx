"use client";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import type { MeetingMock } from "../surfaces/meetingModel";
import { splitLeadingH1 } from "./workspaceFrontPage";
import { type as ty } from "./tokens";

export function meetingHeader(body: string, meeting?: MeetingMock) {
  const heading = splitLeadingH1(body).title;
  const generic = !heading || /^(google meet|zoom|microsoft teams|teams|live)?\s*meeting$/i.test(heading);
  const title = meeting?.title_custom || (!generic ? heading : meeting?.title) || heading || "Meeting";
  const when = meeting?.start_time || meeting?.scheduled_at;
  const date = when ? new Date(when) : null;
  const status = meeting?.live_status || (meeting?.status === "live" ? "live" : meeting ? "past" : "");
  const metadata = [
    meeting?.platform,
    date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
    }).format(date) : undefined,
    status ? status.replaceAll("_", " ") : undefined,
    meeting?.participants.length ? `${meeting.participants.length} participant${meeting.participants.length === 1 ? "" : "s"}` : undefined,
  ].filter(Boolean).join(" · ");
  return { title, metadata };
}

export function MeetingPageHeader({ meetingId, body, path }: { meetingId: string; body: string; path: string }) {
  const meetings = useLiveMeetings();
  // Bind to the document's meeting, never the currently selected chat or another live call.
  const meeting = meetings.find(m => m.id === meetingId || m.native_id === meetingId);
  const { title, metadata } = meetingHeader(body, meeting);
  // ONE header row: the title, then platform · date · status muted beside it. The title keeps its
  // width (up to 60% of the row) and the metadata takes what is left, truncating with the full text
  // on hover; only when not even 80px are left does anything wrap, and nothing ever overlaps the
  // header's icons. Delete sits in the document header's icon group, and the player row is that header's
  // own full-width second line (both in PagesPanel).
  // A zero basis: in the wrapping document header this shares the first line with the icon group
  // instead of claiming the whole line and pushing the icons onto a row of their own.
  return <div style={{ flex: "1 1 0%", minWidth: 0 }}>
    <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: 8, rowGap: 4, minWidth: 0 }}>
      <div data-doc-name title={path} style={{ ...ty.title, fontSize: 13.5, flex: "0 0 auto", maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
      {metadata && <div data-meeting-metadata title={metadata} style={{ ...ty.meta, flex: "1 1 0%", minWidth: 80, color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{metadata}</div>}
    </div>
  </div>;
}
