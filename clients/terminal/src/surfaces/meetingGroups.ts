/** meetingGroups — MEETING IDENTITY ≠ RUN (design-spec meeting-lifecycle-v2 §v4, fixes BUG-1).
 *
 *  meeting-api returns one row per bot-launch/plan; a weekly meeting or a re-sent link therefore
 *  shows up as SEVERAL rows sharing one identity. Rendering rows raw makes a future/live meeting's
 *  finished sibling runs leak into "recorded" and duplicates every synced meeting. This module
 *  groups rows into MEETINGS:
 *
 *    identity = data.calendar_uid          (calendar-imported — survives link changes)
 *             ⊳ platform + native id       (same link = same meeting)
 *             ⊳ the row id                 (a one-off run is its own meeting)
 *
 *  Within a group the run CARRYING THE MEETING'S STATE is `current` (live wins, else the upcoming
 *  plan, else the newest finished run); every OTHER finished run is history (`pastRuns` also
 *  includes a finished `current`, so review surfaces read one list). Pure + offline-tested. */
import { meetingPhase, type MeetingMock, type MeetingPhase } from "./meetingModel";

export interface MeetingGroup {
  key: string;
  /** the run that carries the meeting's state right now (live ⊳ prep ⊳ newest post) */
  current: MeetingMock;
  /** `meetingPhase(current)` — the group's phase, precomputed for the zone splits */
  phase: MeetingPhase;
  /** every run in the group, current first, then the rest newest-first */
  runs: MeetingMock[];
  /** finished (post-phase) runs, newest-first — recap/history material */
  pastRuns: MeetingMock[];
}

export function meetingGroupKey(m: Pick<MeetingMock, "id" | "native_id" | "platform" | "calendar_uid">): string {
  if (m.calendar_uid) return `cal:${m.calendar_uid}`;
  if (m.native_id) return `native:${m.platform}:${m.native_id}`;
  return `row:${m.id}`;
}

/** Newest-first ordering key for a run: when it actually ran, else when it's due. */
const runStamp = (m: MeetingMock): string => m.start_time ?? m.scheduled_at ?? "";

const PHASE_RANK: Record<MeetingPhase, number> = { live: 0, prep: 1, post: 2 };

/** Pick the run carrying the meeting's state: live ⊳ prep ⊳ post; ties break newest-first
 *  (except prep, where the SOONEST upcoming plan is the meeting's next occurrence). */
function pickCurrent(runs: MeetingMock[]): MeetingMock {
  let best = runs[0];
  let bestPhase = meetingPhase(best);
  for (const r of runs.slice(1)) {
    const p = meetingPhase(r);
    if (PHASE_RANK[p] < PHASE_RANK[bestPhase]) { best = r; bestPhase = p; continue; }
    if (PHASE_RANK[p] > PHASE_RANK[bestPhase]) continue;
    if (p === "prep") {
      // two plans on one identity: the sooner scheduled one is next (unscheduled last)
      const a = r.scheduled_at ?? "￿", b = best.scheduled_at ?? "￿";
      if (a < b) best = r;
    } else if (runStamp(r) > runStamp(best)) best = r;
  }
  return best;
}

/** Group rows into meetings. Input order is the API's (newest-first) and is preserved for
 *  group discovery, so groups come out roughly newest-first too. */
export function groupMeetings(list: MeetingMock[]): MeetingGroup[] {
  const byKey = new Map<string, MeetingMock[]>();
  const order: string[] = [];
  for (const m of list) {
    const k = meetingGroupKey(m);
    const bucket = byKey.get(k);
    if (bucket) bucket.push(m);
    else { byKey.set(k, [m]); order.push(k); }
  }
  return order.map((key) => {
    const rows = byKey.get(key)!;
    const current = pickCurrent(rows);
    const rest = rows.filter((r) => r !== current).sort((a, b) => runStamp(b).localeCompare(runStamp(a)));
    const pastRuns = rows.filter((r) => meetingPhase(r) === "post")
      .sort((a, b) => runStamp(b).localeCompare(runStamp(a)));
    return { key, current, phase: meetingPhase(current), runs: [current, ...rest], pastRuns };
  });
}
