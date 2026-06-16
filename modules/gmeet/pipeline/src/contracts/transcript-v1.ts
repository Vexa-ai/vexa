/**
 * transcript.v1 — the speaker-attribution→collector contract (CANONICAL).
 *
 *   separated-transcript.v1 (opaque speakerKey)  +  capture.v1 name hints
 *        ──►  speaker-attribution  ──►  transcript.v1 (RESOLVED speaker name)
 *
 * Identical segment geometry to separated-transcript.v1 — same text, timing and
 * words — but the opaque `speakerKey` is now resolved to a real participant
 * `speaker` (or left as the cluster id when no evidence bound it, flagged by
 * `source`). This is the last brick before the collector; downstream stores and
 * renders this verbatim.
 *
 * One contract, two upstream key-sources (multistream channel id ‖ mixed cluster
 * id) — speaker-attribution stays agnostic; the resolved `speaker` is all the
 * collector sees.
 */

/** A single word with meeting-clock timing (seconds). Mirrors separated-transcript.v1. */
export interface TimestampedWord {
  word: string;
  start: number;
  end: number;
}

/** A run of consecutive words attributed to ONE resolved speaker. */
export interface TranscriptSegment {
  /** Resolved participant display name — or the opaque key when unresolved. */
  speaker: string;
  /** Provenance: the upstream opaque key (channel id or diarizer cluster id). */
  speakerKey: string;
  text: string;
  start: number;        // seconds, meeting clock
  end: number;          // seconds, meeting clock
  words: TimestampedWord[];
  /** How `speaker` was bound. provisional-cluster-id = unresolved (speaker == speakerKey).
   *  glow-bound = named AT THE SOURCE in capture (gmeet glow), carried through the
   *  per-speaker pipeline — no post-hoc attribution. */
  source: 'window-match' | 'cluster-vote' | 'provisional-cluster-id' | 'channel-map' | 'glow-bound';
  /** 1.0-ish for unambiguous matches; lower for vote majorities; 0 provisional. */
  confidence: number;
  /** Carried from separated-transcript.v1 for the collector. */
  topology: 'per-participant' | 'mixed';
}

/** Per-meeting meta carried alongside the segment stream. */
export interface TranscriptMeta {
  platform?: string;
  nativeMeetingId?: string | number;
  language?: string | null;
}

/**
 * The contract-out port. speaker-attribution emits segments here; the collector
 * implements it (persist + render).
 */
export interface TranscriptSink {
  segment(seg: TranscriptSegment): void;
  /** Optional LIVE PARTIAL for seg.speakerKey (resolved-name draft); empty text
   *  clears it. Additive — confirmed-only consumers omit it. */
  draft?(seg: TranscriptSegment): void;
  finalize(): void | Promise<void>;
}
