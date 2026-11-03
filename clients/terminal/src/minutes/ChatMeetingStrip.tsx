"use client";
/** THE MEETING THIS CHAT STARTED, in its header (founder, 2026-10-13): a bot sent, a call started or
 *  a voice note recorded from a chat makes that chat the meeting's session.
 *
 *  ONE CHIP, AND ONLY WHILE IT IS HAPPENING (founder, 2026-10-14, on a live call: "total mess here").
 *  The header used to carry the meeting's name, its status word, "Stop bot" and "Transcript" beside the
 *  title, which they overlapped. Now it is one chip — "● Live · <meeting>" — and its click opens the
 *  meeting view in the right panel, where the transcript and Stop bot live. An ended meeting has no
 *  chip: it is in the rail and its page. */
import type { MeetingMock } from "../surfaces/meetingModel";
import { HAPPENING, meetingStatusOf } from "./meetingActions";
import "./minutes.css";

const STATE_WORD: Record<string, string> = {
  requested: "Bot requested", joining: "Joining", awaiting_admission: "Waiting to be let in",
  needs_help: "Bot needs help", active: "Live",
};

const titleOf = (m: MeetingMock) => (m as { title?: string }).title || "Meeting";

export function LiveMeetingChip({ meeting: m, onOpen }: { meeting: MeetingMock; onOpen: () => void }) {
  const status = meetingStatusOf(m);
  if (!HAPPENING.has(status)) return null;
  const word = m.platform === "voice_note" && status === "active" ? "Recording" : (STATE_WORD[status] || "Live");
  const title = titleOf(m);
  return (
    <button type="button" className="vx-live-chip" data-live-meeting={m.id} data-state={status}
      title={`${word} · ${title} — open the meeting (transcript, Stop bot)`} onClick={onOpen}>
      <span className="vx-live-chip-dot" aria-hidden />
      <span className="vx-live-chip-text">{word} · {title}</span>
    </button>
  );
}

/** WHICH MEETING TO OPEN IN THE PANEL BY ITSELF: the chat's meeting while it happens, once per chat
 *  and meeting — a reader who closes the tab keeps it closed. Returns the key to remember, or null. */
export function meetingToOpen(chatId: string, m: MeetingMock | undefined, opened: ReadonlySet<string>): string | null {
  if (!m || !HAPPENING.has(meetingStatusOf(m))) return null;
  const key = `${chatId}:${m.id}`;
  return opened.has(key) ? null : key;
}
