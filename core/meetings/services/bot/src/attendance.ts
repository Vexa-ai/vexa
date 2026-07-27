/**
 * The attendance accumulator — who was in the room, and for how long.
 *
 * The roster is already known: every platform's speakers module enumerates named participant
 * tiles on its live loop, because speaker attribution needs it (`scanTiles()` in gmeet,
 * `getState().tiles` in zoom, the participants store in jitsi, the identity cache in teams).
 * That enumeration was consumed for one question — who is talking — and the rest discarded.
 * This keeps it.
 *
 * INTERVALS, not a first/last pair: someone who joins, drops, and rejoins is present for the two
 * stretches they were actually here, and `present_seconds` must not swallow the gap between them.
 *
 * CHANGE-DRIVEN, not sampled. The bridge calls `observe` only when the roster actually differs,
 * so a 90-minute meeting costs one call per arrival and one per departure — not one per poll.
 * That is a hard requirement, not an optimisation: the sibling `speaker_events` channel was
 * written per-tick and measured 155 MB across one account (collector/projection.py), which is why
 * it now lives in LIST_OMIT_KEYS. An open interval closes at `report()` time, so a participant
 * who never leaves is still measured to the end of the meeting without a heartbeat.
 */

/** One stretch a participant was continuously visible: [from, to] as ISO instants. */
export type AttendanceInterval = [string, string];

/** One participant's attendance across the whole meeting. */
export interface ParticipantAttendance {
  name: string;
  first_seen: string;       // ISO — first time this name appeared
  last_seen: string;        // ISO — last time it was still there
  present_seconds: number;  // summed across intervals; excludes any gap they were away for
  intervals: AttendanceInterval[];
}

export interface AttendanceReporter {
  /** Record the roster as it now stands. Call ONLY when it changed. Never throws. */
  observe(names: readonly string[], nowMs?: number): void;
  /** The lifecycle.v1 fragment for the terminal event, or undefined if nobody was ever seen.
   *  Closes any still-open interval at call time. Shaped for `OrchestratorDeps.terminalExtras`. */
  report(nowMs?: number): Record<string, unknown> | undefined;
  /** Distinct participants seen so far — the counter a periodic log line reads. */
  seen(): number;
}

/** A display name we refuse to treat as a person. The capture modules already filter their own
 *  junk, but this is the last hop before the record is durable, so it re-checks. */
function isUsableName(name: unknown): name is string {
  return typeof name === 'string' && name.trim().length > 0 && name.trim().length <= 120;
}

interface Tracked {
  first: number;            // ms — first appearance
  last: number;             // ms — end of the most recent closed interval, or the last known presence
  openedAt: number | null;  // ms — start of the interval currently open, or null when away
  closed: Array<[number, number]>;
}

export function createAttendanceReporter(
  now: () => number = () => Date.now(),
): AttendanceReporter {
  const byName = new Map<string, Tracked>();

  return {
    seen: () => byName.size,

    observe(names: readonly string[], nowMs?: number): void {
      try {
        const t = nowMs ?? now();
        const present = new Set<string>();
        for (const raw of names ?? []) {
          if (!isUsableName(raw)) continue;
          present.add(raw.trim());
        }

        for (const name of present) {
          const cur = byName.get(name);
          if (!cur) {
            byName.set(name, { first: t, last: t, openedAt: t, closed: [] });
          } else if (cur.openedAt === null) {
            cur.openedAt = t;   // rejoined — a NEW interval, so the away gap is never counted
          }
        }

        // Anyone tracked-and-open but absent from this roster has left. Their interval closes at
        // `t` — the moment we observed the absence, which is this reporter's best evidence.
        for (const [name, cur] of byName) {
          if (present.has(name) || cur.openedAt === null) continue;
          cur.closed.push([cur.openedAt, t]);
          cur.openedAt = null;
          cur.last = t;
        }
      } catch { /* an accumulator must never break the loop that reports to it */ }
    },

    report(nowMs?: number): Record<string, unknown> | undefined {
      try {
        if (byName.size === 0) return undefined;
        const t = nowMs ?? now();
        const iso = (ms: number) => new Date(ms).toISOString();

        const participants: ParticipantAttendance[] = [...byName.entries()]
          .map(([name, cur]) => {
            // Still here when the meeting ended: close at report time, so a participant who never
            // leaves is measured to the end rather than to their arrival.
            const spans = cur.openedAt === null ? cur.closed : [...cur.closed, [cur.openedAt, t] as [number, number]];
            const lastMs = spans.length ? spans[spans.length - 1][1] : cur.last;
            return {
              name,
              first_seen: iso(cur.first),
              last_seen: iso(lastMs),
              present_seconds: Math.round(spans.reduce((n, [a, b]) => n + Math.max(0, b - a), 0) / 1000),
              intervals: spans.map(([a, b]) => [iso(a), iso(b)] as AttendanceInterval),
            };
          })
          .sort((a, b) => a.first_seen.localeCompare(b.first_seen) || a.name.localeCompare(b.name));

        return {
          attendance: {
            participants,
            observed_to: iso(t),
          },
        };
      } catch {
        return undefined;
      }
    },
  };
}
