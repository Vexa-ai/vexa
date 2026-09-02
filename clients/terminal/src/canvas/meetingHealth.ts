// THE BOT'S STATE, as the reader sees it. Pure helpers (testable, no React) that turn the
// `diagnostics` already tracked per live meeting — plus the meeting's own status — into one
// explicit verdict, so the terminal says where the bot is instead of silently rendering stale data.
//
// The vocabulary is deliberately the BOT'S (founder ruling, 2026-09-02): at the door · admitted ·
// left. It used to be the feed's — "Waiting for transcript — no new lines for 24s" — which reads
// as the product working on something, and the product no longer works on anything during a
// meeting (PRD decision 34). Nothing here may imply processing.
import type { MeetingDiagnosticIssue, MeetingState } from "./types";

/** A live transcript is considered STALE when no new line has landed for this long. */
export const STALE_MS = 20_000;

export type MeetingHealthKind = "ok" | "at-door" | "ended" | "disconnected" | "stalled" | "error";

/** The bot is on its way in but not in the room yet — the window a reader most wants named, and
 *  the one the feed alone cannot see (no line has landed, so nothing is "stale"). */
const DOOR_STATUSES = new Set(["requested", "joining", "awaiting_admission"]);
const NEEDS_HELP = "needs_help";

export interface MeetingHealth {
  kind: MeetingHealthKind;
  /** ms since the last transcript line (when known) — drives the ticking "no new lines for Ns". */
  staleForMs?: number;
  reconnects: number;
  /** The most recent feed issue (stream/parse), surfaced even when the headline is something else. */
  latestIssue?: MeetingDiagnosticIssue;
  /** `at-door` only: the platform is asking a human to let the bot in. */
  needsHelp?: boolean;
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
  status = "",
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

  // At the door: the bot has been sent but is not in the room yet. Judged from the MEETING's own
  // status, never guessed from the absence of lines — silence means "not admitted" and "admitted
  // into a quiet room" alike, and only the status tells them apart.
  const s = status.toLowerCase();
  if ((DOOR_STATUSES.has(s) || s === NEEDS_HELP) && d.lastTranscriptAt == null) {
    return { kind: "at-door", reconnects, latestIssue, staleForMs, needsHelp: s === NEEDS_HELP };
  }

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

/** THE HEADLINE — one line naming where the BOT is. Pure so the wording is testable without React,
 *  and so there is exactly one place it can drift.
 *
 *  Never "waiting for transcript", never any word that could read as the product processing
 *  something: it reports a bot in a room, and that is all there is to report. */
export function botStateHeadline(health: MeetingHealth): string {
  switch (health.kind) {
    case "at-door":
      return health.needsHelp ? "Bot needs someone to let it in" : "Bot at the door";
    case "ended":
      return "Bot left";
    case "disconnected":
      return "Reconnecting to the bot\u2026";
    case "stalled":
      // It HAS been admitted and HAS been heard from — `stalled` cannot fire before a first line —
      // so this is a quiet room, not a stuck product. Say the silence, and how long it has run.
      return `Bot admitted \u00b7 no words for ${health.staleForMs != null ? formatElapsed(health.staleForMs) : "a while"}`;
    default:
      return "Bot feed error";
  }
}
