/** EVERY LIVE MEETING AT THE TOP OF HOME (founder, live app.dev 2026-10-18, on Home during a live Vexa
 *  call: "ongoing meeting should be there I think").
 *
 *  Why it was missing: the Now strip read live calls only from the desktop's workspace tiles
 *  (`live_call` per workspace), which a call reaches only through the workspace its chat is aimed at,
 *  one per workspace, with a bounded number of live checks — a call in a chat of his own, or one the
 *  tiles did not reach, was simply not there, while Meetings (the meetings store) listed it as Live.
 *  The rows now come from both: the meetings store (every live meeting the reader may open — Vexa calls
 *  and bot meetings alike) and the tiles, merged by meeting.
 *
 *  Each row: the call's name, who is in, how long, and one verb — Rejoin (a Vexa call you are not
 *  connected to), Open (a bot meeting), or, when this tab is already in the call, "You're in this call"
 *  with Open (the dock has the controls; nothing is duplicated). Pure. */
import type { MeetingMock } from "../../../surfaces/meetingModel";
import { callLabel } from "../../callsList";
import { liveMeetings, peopleOf } from "../../shell2/meetings";
import { rowLabel } from "../../shell2/callNames";

export interface TileLive { session?: string; meeting?: string; line?: string; people?: number; since?: number; where?: string }
export interface NowLiveRow {
  key: string; name: string; people: string[]; count?: number; sinceMs?: number;
  session?: string; meetingId?: string; vexa: boolean; inThis: boolean; where?: string;
}

const ms = (iso?: string) => { const t = iso ? Date.parse(iso) : NaN; return Number.isFinite(t) ? t : undefined; };

export function nowLiveRows(tiles: readonly TileLive[], meetings: readonly MeetingMock[],
  active: { session: string; meetingId?: number | string | null } | null): NowLiveRow[] {
  const out: NowLiveRow[] = [];
  const byMeeting = new Map<string, NowLiveRow>();
  const isIn = (session?: string, meetingId?: string) => !!active && (
    (!!session && active.session === session) || (!!meetingId && active.meetingId != null && String(active.meetingId) === meetingId));
  for (const m of liveMeetings(meetings)) {
    const vexa = m.platform === "vexa_call";
    const row: NowLiveRow = { key: `m:${m.id}`, name: rowLabel(m, callLabel(m)), people: peopleOf(m), sinceMs: ms(m.start_time) ?? ms(m.created_at),
      session: m.call_session, meetingId: m.id, vexa, inThis: isIn(m.call_session, m.id) };
    out.push(row);
    byMeeting.set(m.id, row);
  }
  for (const t of tiles) {
    const known = t.meeting ? byMeeting.get(String(t.meeting)) : undefined;
    if (known) {
      if (!known.session && t.session) known.session = t.session;
      if (known.count == null && typeof t.people === "number") known.count = t.people;
      known.where = known.where ?? t.where;
      known.inThis = known.inThis || isIn(t.session, known.meetingId);
      continue;
    }
    if (t.session && out.some((r) => r.session === t.session)) continue;
    out.push({ key: `t:${t.session ?? t.meeting ?? out.length}`, name: t.line || "Call", people: [], count: t.people,
      sinceMs: t.since ? t.since * 1000 : undefined, session: t.session, meetingId: t.meeting ? String(t.meeting) : undefined,
      vexa: !!t.session, inThis: isIn(t.session, t.meeting ? String(t.meeting) : undefined), where: t.where });
  }
  // the call you are in first, then the newest
  return out.sort((a, b) => Number(b.inThis) - Number(a.inThis) || (b.sinceMs ?? 0) - (a.sinceMs ?? 0));
}
