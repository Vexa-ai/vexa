/** A LIVE MEETING IS ALWAYS PINNED (founder, UI lab 2026-10-14; UI-DECISIONS item 10). While a
 *  meeting runs, its tab is FIRST in the right panel of every chat — not only the chat that sent the
 *  bot — marked live and with no close, so its tools and transcript are one click away anywhere.
 *  When it ends the strip is the chat's own again (the meeting stays reachable from its chat and
 *  from Calls). Derived on every render from the meetings store: nothing is written to any chat. */
import type { MeetingMock } from "../surfaces/meetingModel";
import { HAPPENING, meetingStatusOf } from "./meetingActions";
import type { Page } from "./types";

export function pinLive(pages: Page[], meetings: readonly MeetingMock[]): Page[] {
  const live = meetings.filter((m) => HAPPENING.has(meetingStatusOf(m)));
  if (!live.length) return pages;
  const ids = new Set(live.map((m) => String(m.id)));
  const pinned: Page[] = live.map((m) => ({ kind: "meeting", path: String(m.id), label: m.title || "Meeting", live: true, permanent: true }));
  return [...pinned, ...pages.filter((p) => !(p.kind === "meeting" && ids.has(p.path)))];
}
