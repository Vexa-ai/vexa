/**
 * The STT degradation accumulator — what turns "the transcript is empty" into "the transcript is
 * empty BECAUSE the backend refused, and here is its own answer".
 *
 * The faults are already typed and attributed by the time they reach the composition root
 * (`TranscriptionError` from @vexa/transcribe-whisper: `kind`, `status`, `detail`, `retryable`),
 * and the pipelines already hand every one of them to `onError`. What was missing is the last
 * hop: the root logged them to a console nobody reads after the container exits, so a meeting
 * whose STT backend was dead completed indistinguishable from a silent room — the #807 shape,
 * and the reason the 2026-07-19 exhausted-token deployment produced zero transcripts in silence.
 *
 * This collapses a storm into ONE report, and carries it on TWO channels with different jobs:
 *
 *   DURABLE — `report()` merges a per-kind summary onto the TERMINAL lifecycle.v1 event. It is the
 *   record that outlives the container, and it is the only channel that can carry a final count.
 *
 *   LIVE — `announce` emits a bounded notice the moment a kind first refuses, so the meeting says
 *   WHY while it is still running. Terminal-only reporting meant a 60-minute meeting spent 60
 *   minutes reading `active` with an empty transcript and no reason — which is the #807 shape
 *   restated, not fixed. Announcing is rate-limited by construction: once per kind, then only as
 *   the count crosses 10/100/1000, so a dead backend costs at most 4 notices per kind however many
 *   chunks it refuses.
 */

/** The structural shape of a @vexa/transcribe-whisper TranscriptionError, matched without
 *  importing it: the accumulator must classify anything the pipeline hands it, including a
 *  non-STT fault that happens to reach the same seam. */
interface SttFaultLike {
  source?: string;
  kind?: string;
  status?: number;
  detail?: string;
  message?: string;
}

/** One kind of STT failure, and how much of the meeting it ate. */
export interface SttFaultSummary {
  kind: string;                  // payment_required | unauthorized | unavailable | timeout | …
  count: number;                 // how many chunks this kind refused
  status?: number;               // the backend's HTTP status, when it had one
  detail?: string;               // the backend's OWN words (truncated), never our paraphrase
  first_at: string;              // ISO — when the degradation started
}

/** One LIVE notice — the in-flight half. Deliberately smaller than SttFaultSummary: it says what
 *  is refusing right now, not the meeting's final tally (that is `report()`'s job). */
export interface SttFaultNotice {
  kind: string;
  status?: number;
  detail?: string;
  count: number;
}

export interface SttFaultReporter {
  /** Record one fault from a pipeline `onError`. Never throws. */
  record(fault: unknown): void;
  /** The lifecycle.v1 fragment to merge onto the terminal event, or undefined if nothing
   *  degraded. Shaped for `OrchestratorDeps.degraded`. */
  report(): Record<string, unknown> | undefined;
  /** Total faults seen (all kinds) — the counter the periodic log line reads. */
  total(): number;
}

const DETAIL_MAX = 300;

/** True for a fault that came from the STT boundary (P5: the adapter stamps `source`). */
function isSttFault(f: SttFaultLike): boolean {
  return f?.source === 'stt' || typeof f?.kind === 'string';
}

/** The counts at which a continuing storm re-announces. Bounded on purpose: first + these = at
 *  most 4 live notices per kind, however long the backend stays dead. */
const ANNOUNCE_AT = new Set([10, 100, 1000]);

export function createSttFaultReporter(
  log: (m: string) => void = (m) => console.error(m),
  now: () => Date = () => new Date(),
  /** The LIVE channel. Injected, synchronous, and MUST NOT throw or return a floating rejection —
   *  it is called from inside `record()`, on the degraded path, where an unhandled rejection would
   *  take the process down exactly when the meeting most needs the bot alive. */
  announce?: (notice: SttFaultNotice) => void,
): SttFaultReporter {
  const byKind = new Map<string, SttFaultSummary>();
  let total = 0;

  /** Announce only for a fault the STT adapter itself stamped (P5). `isSttFault` is deliberately
   *  permissive so `record()` classifies anything the pipeline hands it — but the LIVE channel is
   *  bounded to the real STT boundary, so a transcript-publish rejection arriving at the same seam
   *  can never be rendered to a user as a model error. */
  const announceIf = (fromStt: boolean, s: SttFaultSummary): void => {
    if (!announce || !fromStt) return;
    announce({ kind: s.kind, status: s.status, detail: s.detail, count: s.count });
  };

  return {
    total: () => total,
    record(fault: unknown): void {
      try {
        const f = (fault ?? {}) as SttFaultLike;
        if (!isSttFault(f)) return;
        const fromStt = f?.source === 'stt';
        total++;
        const kind = f.kind ?? 'unknown';
        const seen = byKind.get(kind);
        if (seen) {
          seen.count++;
          // A storm re-announces only at the thresholds — the count carries the rest.
          if (ANNOUNCE_AT.has(seen.count)) announceIf(fromStt, seen);
          return;
        }
        const detail = (f.detail ?? f.message ?? '').slice(0, DETAIL_MAX) || undefined;
        const summary: SttFaultSummary = { kind, count: 1, status: f.status, detail, first_at: now().toISOString() };
        byKind.set(kind, summary);
        announceIf(fromStt, summary);
        // FIRST of a kind is loud — an operator watching logs should not wait for the terminal
        // event to learn the backend is refusing. Repeats are silent (the count carries them).
        log(`[bot] STT DEGRADED (${kind}${f.status ? ` HTTP ${f.status}` : ''}): ${detail ?? 'no detail'} — transcription is failing; this meeting will be short or empty`);
      } catch { /* an accumulator must never break the path that reports to it */ }
    },
    report(): Record<string, unknown> | undefined {
      if (byKind.size === 0) return undefined;
      const faults = [...byKind.values()].sort((a, b) => b.count - a.count);
      return {
        stt_fault: {
          kinds: faults,
          total: faults.reduce((n, f) => n + f.count, 0),
        },
        // A one-line human summary on the field lifecycle.v1 already carries for free, so an
        // operator reading a raw callback sees it without knowing the new field exists.
        reason: `stt_degraded: ${faults.map((f) => `${f.kind}×${f.count}`).join(', ')}`,
      };
    },
  };
}
