// Failure-surfacing for the live meeting feed. Pure helpers (testable, no React) that turn the
// `diagnostics` already tracked per live meeting into a single explicit health verdict, so the
// terminal can show a loud banner instead of silently rendering stale data.
import type { MeetingDiagnosticIssue, MeetingState } from "./types";

/** A live transcript is considered STALE when no new line has landed for this long. */
export const STALE_MS = 20_000;

export type MeetingHealthKind = "ok" | "ended" | "disconnected" | "stalled" | "error";

export interface MeetingHealth {
  kind: MeetingHealthKind;
  /** ms since the last transcript line (when known) — drives the ticking "no new lines for Ns". */
  staleForMs?: number;
  reconnects: number;
  /** The most recent issue (stream/model/parse), surfaced even when the headline is something else. */
  latestIssue?: MeetingDiagnosticIssue;
}

type Diagnostics = NonNullable<MeetingState["diagnostics"]>;

/** Is this meeting a LIVE feed we should watch for staleness? Recorded/past meetings have no
 *  session and are never "stalled" — they're just done. */
export function isLiveFeed(state: Pick<MeetingState, "meeting"> & { sessionUid?: string }): boolean {
  return Boolean(state.sessionUid);
}

/** Pure stale predicate: given the last transcript timestamp and "now", is the feed stale? */
export function isTranscriptStale(lastTranscriptAt: number | undefined, now: number, staleMs = STALE_MS): boolean {
  if (!lastTranscriptAt) return false; // never saw a line yet → "connecting", not "stalled"
  return now - lastTranscriptAt >= staleMs;
}

/** Collapse diagnostics + clock into one explicit verdict. `live` is false for recorded meetings
 *  (no session_uid) — those never report disconnected/stalled. */
export function meetingHealth(
  diagnostics: Diagnostics | undefined,
  now: number,
  live: boolean,
  staleMs = STALE_MS,
): MeetingHealth {
  const d = diagnostics ?? {};
  const issues = d.issues ?? [];
  const latestIssue = issues.length ? issues[issues.length - 1] : undefined;
  const reconnects = d.reconnects ?? 0;
  const staleForMs = d.lastTranscriptAt != null ? Math.max(0, now - d.lastTranscriptAt) : undefined;

  // Clean end wins over everything — never cry "stalled" for a meeting that ended on purpose.
  if (d.ended) return { kind: "ended", reconnects, latestIssue, staleForMs };

  if (!live) return { kind: "ok", reconnects, latestIssue, staleForMs };

  // A dropped stream is the loudest live failure.
  if (d.liveConnected === false) return { kind: "disconnected", reconnects, latestIssue, staleForMs };

  // Stale transcript: connected but no new lines for a while.
  if (isTranscriptStale(d.lastTranscriptAt, now, staleMs)) {
    return { kind: "stalled", reconnects, latestIssue, staleForMs };
  }

  // Connected and fresh, but a model/parse error is on record — still surface it.
  if (latestIssue) return { kind: "error", reconnects, latestIssue, staleForMs };

  return { kind: "ok", reconnects, latestIssue, staleForMs };
}

export function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}
