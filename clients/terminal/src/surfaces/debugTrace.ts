/** Dev-only session-trace RECORDER — captures the exact ordered inputs the meeting view receives
 *  (SSE meeting-stream events + durable by-id REST responses) so a LIVE session can be replayed
 *  deterministically in an isolated test. The human records ONCE; the recorded fixture drives the
 *  debug loop forever after — the human is never the debug loop.
 *
 *  The invariant these fixtures exist to prove — RELOAD-EQUIVALENCE (convergence):
 *    replaying a trace incrementally must yield the SAME view a fresh mount computes from the final
 *    durable snapshot. Every "correct only after reload" bug is a violation of this invariant, and
 *    lives in the ORDERED SEQUENCE of events — not in any static end-state (which is why seam
 *    fixtures stay green while the live transition fails).
 *
 *  Enabled in dev (NODE_ENV!=production), with ?rec, or localStorage 'vexa.terminal.rec'='1'.
 *  After a session, in the browser console:  __dumpMeetingTrace('<rowId>')  → downloads the fixture. */
export type TraceKind = "sse" | "rest" | "status" | "list" | "view";
export interface TraceEvent {
  seq: number;
  tMs: number;
  kind: TraceKind;
  meetingId: string;
  data: unknown;
}

function enabled(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (process.env.NODE_ENV !== "production") return true;
    if (new URLSearchParams(window.location.search).has("rec")) return true;
    return window.localStorage.getItem("vexa.terminal.rec") === "1";
  } catch {
    return false;
  }
}

let seq = 0;
const buf: TraceEvent[] = [];

/** Append one input event to the trace. Data is deep-cloned (structuredClone-ish) so later
 *  in-place mutation of live state can't rewrite already-recorded events. Non-serializable → skip. */
export function recordTrace(kind: TraceKind, meetingId: string, data: unknown): void {
  if (!enabled()) return;
  try {
    buf.push({ seq: seq++, tMs: Date.now(), kind, meetingId: String(meetingId), data: JSON.parse(JSON.stringify(data)) });
  } catch {
    /* non-serializable payload — drop rather than corrupt the trace */
  }
}

export function getTrace(meetingId?: string): TraceEvent[] {
  return meetingId ? buf.filter((e) => e.meetingId === String(meetingId)) : buf.slice();
}

if (typeof window !== "undefined") {
  (window as unknown as Record<string, unknown>).__dumpMeetingTrace = (meetingId?: string) => {
    const events = getTrace(meetingId);
    const json = JSON.stringify(
      { recordedAt: new Date().toISOString(), meetingId: meetingId ?? "all", count: events.length, events },
      null,
      2,
    );
    try {
      const blob = new Blob([json], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `meeting-trace-${meetingId ?? "all"}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch {
      /* headless / no DOM download — the console copy below is the fallback */
    }
    // eslint-disable-next-line no-console
    console.log(`[trace] dumped ${events.length} events for meeting ${meetingId ?? "all"}`);
    return json;
  };
  (window as unknown as Record<string, unknown>).__clearMeetingTrace = () => {
    buf.length = 0;
    seq = 0;
    // eslint-disable-next-line no-console
    console.log("[trace] cleared");
  };
}
