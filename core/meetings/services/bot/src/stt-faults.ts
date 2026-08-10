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
 * This collapses a storm into ONE report. A dead backend faults on every chunk (18 in a 2-minute
 * live run), so counting per kind and reporting once — on the terminal lifecycle event — is both
 * the honest summary and the only shape that cannot flood the control plane.
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

export function createSttFaultReporter(
  log: (m: string) => void = (m) => console.error(m),
  now: () => Date = () => new Date(),
): SttFaultReporter {
  const byKind = new Map<string, SttFaultSummary>();
  let total = 0;

  return {
    total: () => total,
    record(fault: unknown): void {
      try {
        const f = (fault ?? {}) as SttFaultLike;
        if (!isSttFault(f)) return;
        total++;
        const kind = f.kind ?? 'unknown';
        const seen = byKind.get(kind);
        if (seen) {
          seen.count++;
          return;
        }
        const detail = (f.detail ?? f.message ?? '').slice(0, DETAIL_MAX) || undefined;
        byKind.set(kind, { kind, count: 1, status: f.status, detail, first_at: now().toISOString() });
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
