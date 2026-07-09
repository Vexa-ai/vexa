"use client";
/** Sessions — the chat-session TITLE helper. The separate "Sessions" LEFT list was retired (owner
 *  cleanup): the chat lives in the persistent right rail, so a nav item that only listed past sessions
 *  was redundant. This module now just exports `sessionTitle` (the chat rail's session picker reuses it)
 *  and re-exports `SessionSummary`. */
import { type SessionSummary } from "./sessionsApi";
import { ONBOARDING_KICKOFF_MARK } from "../canvas/actions";
export type { SessionSummary } from "./sessionsApi";  // re-exported for the chat surface

const truncateSessionId = (session: string) => session.length > 18 ? `${session.slice(0, 18)}...` : session;

function meetingLabel(value: string): string {
  const meeting = (value.split("·").pop()?.trim() || value.trim()).replace(/^["'\\]+|["'\\.)]+$/g, "");
  return meeting ? `Meeting ${meeting}` : "Meeting";
}

function compactTitle(title: string): string {
  const raw = title.trim().replace(/^["']|["']$/g, "");
  const activeRef = raw.match(/^Active meeting reference:\s*@meeting:([A-Za-z0-9._~%+@:/=-]+)/);
  if (activeRef) return meetingLabel(activeRef[1]);
  const activeMeeting = raw.match(/^Active meeting ([A-Za-z0-9._~%+@:/=-]+)/);
  if (activeMeeting) return meetingLabel(activeMeeting[1]);
  const legacyCopilot = raw.match(/^You are the copilot for a live meeting \((?:\\)?["']([^"']+)/);
  if (legacyCopilot) return meetingLabel(legacyCopilot[1]);
  return raw;
}

export const sessionTitle = (s: SessionSummary) => {
  const title = s.title?.trim();
  // A session titled by the onboarding kickoff (the first message) must not show the raw prompt — fall
  // back to the session id (e.g. "main").
  if (title && !title.includes(ONBOARDING_KICKOFF_MARK)) return compactTitle(title);
  return truncateSessionId(s.session);
};

