/**
 * Transcript gaps — the last hop of "no audio loss without a record" (prod-cutover R4).
 *
 * Every lane records the stretches of captured audio it will never transcribe (a window the STT
 * refused past its retries, the held-audio bound, a lane closed with audio still owed) as
 * `MissingRange`s by capture time. Nothing outside the lane read them, so a gap died with the
 * container and the meeting looked like a quiet room. This turns them into the `transcript_gaps`
 * fragment the TERMINAL lifecycle.v1 event carries — additive, exactly as `stt_fault` is
 * (lifecycle.v1 ingests additionalProperties) — and meeting-api persists it on the meeting.
 *
 * Times are epoch SECONDS, the unit transcript.v1 segments use, plus the canonical ISO stamps, so a
 * reader places a gap on the same axis as the words around it.
 */
import type { MissingRange } from './pipeline.js';

export interface TranscriptGap {
  start: number;                 // epoch seconds
  end: number;                   // epoch seconds
  absolute_start_time: string;   // ISO
  absolute_end_time: string;     // ISO
  speaker?: string;              // the lane's name for whose audio it was, when it had one
  reason: string;                // why it was not transcribed, in the lane's words
}

/** One terminal event is a bounded document: a pathological meeting reports its first 500 gaps
 *  (the total seconds stay countable from them; the log line carries the full count). */
export const MAX_GAPS_PER_EVENT = 500;
const REASON_MAX = 200;

/** The lifecycle.v1 fragment for the terminal event, or undefined when nothing was lost. */
export function transcriptGapsReport(ranges: readonly MissingRange[] | undefined): Record<string, unknown> | undefined {
  const valid = (ranges ?? []).filter((r) => Number.isFinite(r.startMs) && Number.isFinite(r.endMs) && r.endMs > r.startMs);
  if (valid.length === 0) return undefined;
  const gaps: TranscriptGap[] = [...valid]
    .sort((a, b) => a.startMs - b.startMs)
    .slice(0, MAX_GAPS_PER_EVENT)
    .map((r) => ({
      start: r.startMs / 1000,
      end: r.endMs / 1000,
      absolute_start_time: new Date(r.startMs).toISOString(),
      absolute_end_time: new Date(r.endMs).toISOString(),
      ...(r.speakerName ? { speaker: r.speakerName } : {}),
      reason: String(r.reason ?? 'unspecified').slice(0, REASON_MAX),
    }));
  return { transcript_gaps: gaps };
}

/** Merge the degraded fragments the terminal event carries (stt_fault + transcript_gaps). */
export function mergeDegraded(
  ...parts: Array<Record<string, unknown> | undefined>
): Record<string, unknown> | undefined {
  const present = parts.filter((p): p is Record<string, unknown> => !!p);
  if (present.length === 0) return undefined;
  return Object.assign({}, ...present);
}

/** One loud line at the end of a meeting that lost audio — the operator's counter. */
export function describeGaps(ranges: readonly MissingRange[] | undefined): string | undefined {
  const valid = (ranges ?? []).filter((r) => r.endMs > r.startMs);
  if (valid.length === 0) return undefined;
  const seconds = valid.reduce((n, r) => n + (r.endMs - r.startMs) / 1000, 0);
  return `[bot] TRANSCRIPT GAPS: ${valid.length} range(s), ${seconds.toFixed(1)}s of captured audio not transcribed — ` +
    `recorded on the meeting (transcript_gaps)`;
}
