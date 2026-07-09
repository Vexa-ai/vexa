/**
 * transcript.v1 — the TS view of the SEALED contract.
 *
 * SSOT is the JSON Schema at meetings/contracts/transcript.v1/transcript.schema.json
 * (Draft 2020-12). This is the pipeline's typed view of that schema's TranscriptSegment
 * + the sink port it emits to; the replay golden (pipeline-conformance.test.ts) validates
 * the pipeline's actual output against the SSOT, so this view can never silently drift.
 *
 * The pipeline emits segments; the HOST (bot) wraps them into the bus envelopes
 * (SessionStart / Transcription / SessionEnd / MutableBundle — also in the schema).
 */

/** How `speaker` was attributed (schema $defs/Source). */
export type Source = 'glow-bound' | 'provisional-cluster-id' | 'caption' | 'merged';

/** A single word with meeting-clock timing (seconds) — schema $defs/Word. */
export interface TimestampedWord {
  word: string;
  start: number;
  end: number;
  probability?: number;
}

/** One speaker-attributed utterance — schema $defs/TranscriptSegment.
 *  Required: segment_id, speaker, text, start, end, completed. */
export interface TranscriptSegment {
  /** stable id (the pipeline keys it `{speaker_key}:{startMs}`; the host may re-key with session_uid). */
  segment_id: string;
  /** resolved display name (glow-bound) or a provisional label. */
  speaker: string;
  /** per-channel turn key (provenance). */
  speaker_key?: string;
  text: string;
  start: number;            // seconds, meeting clock
  end: number;              // seconds, meeting clock
  language?: string | null;
  /** true = confirmed, false = live draft/pending. */
  completed: boolean;
  absolute_start_time?: string;
  absolute_end_time?: string;
  source?: Source;
  /** 1.0 for glow-bound, 0 for provisional. */
  confidence?: number;
  words?: TimestampedWord[];
}

/** Per-meeting meta the host carries alongside the segment stream. */
export interface TranscriptMeta {
  platform?: string;
  nativeMeetingId?: string | number;
  language?: string | null;
}

/**
 * The contract-out port. The pipeline emits segments here; the host (collector /
 * renderer) implements it (persist + render + wrap into the bus envelopes).
 */
export interface TranscriptSink {
  segment(seg: TranscriptSegment): void;
  /** Optional LIVE PARTIAL for seg.speaker_key (completed:false); empty text clears it.
   *  Additive — confirmed-only consumers omit it. */
  draft?(seg: TranscriptSegment): void;
  finalize(): void | Promise<void>;
}
