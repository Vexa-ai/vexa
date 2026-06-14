/**
 * separated-transcript.v1 — the pipeline→attribution contract (CANONICAL).
 *
 * Output of the two transcription-pipeline bricks, input to speaker-attribution:
 *
 *   capture.v1 (mixed)          → mixed-pipeline (diarizer)        ┐
 *                                                                  ├→ separated-transcript.v1 → speaker-attribution → transcript.v1
 *   capture.v1 (per-participant)→ multistream-pipeline (channels) ┘
 *
 * The seam exists because BOTH topologies converge here: a segment of text on
 * the meeting clock, labelled by an OPAQUE speaker key — never a resolved name.
 * Multistream sets the key to the capture channel id ("speaker-3"); mixed sets
 * it to the diarization cluster id ("spk_0"). Resolving that key to a real
 * participant name is the next brick's job (speaker-attribution), so the two
 * pipelines stay identity-free and the attribution logic single-sources.
 *
 * One contract, two producers, one consumer — the split the MANIFEST §2 note
 * deferred ("channel-labeler vs diarizer … one oracle"): the strategies differ,
 * but the contract-out they emit is identical, which is exactly why it is one
 * contract and the bricks that produce it can be two.
 */

/** A single word with meeting-clock timing (seconds). */
export interface TimestampedWord {
  word: string;
  start: number;  // seconds, meeting clock
  end: number;    // seconds, meeting clock
}

/** A run of consecutive words attributed to one OPAQUE speaker key. */
export interface SeparatedSegment {
  /**
   * Opaque stream/cluster identity — NOT a resolved participant name.
   * multistream → capture channel id (e.g. "speaker-3");
   * mixed       → diarization cluster id (e.g. "spk_0").
   * speaker-attribution maps this to a real name.
   */
  speakerKey: string;
  text: string;
  start: number;        // seconds, meeting clock
  end: number;          // seconds, meeting clock
  words: TimestampedWord[];
  /** Which strategy produced this segment — provenance for the oracle. */
  topology: 'per-participant' | 'mixed';
  confidence?: number;  // 0..1 when the STT/diarizer reports it
}

/** Per-meeting meta carried alongside the segment stream. */
export interface SeparatedTranscriptMeta {
  platform?: string;
  nativeMeetingId?: string | number;
  language?: string | null;
}

/**
 * The contract-out port. The pipeline bricks emit segments here;
 * speaker-attribution implements it (consume + resolve names).
 */
export interface SeparatedTranscriptSink {
  segment(seg: SeparatedSegment): void;
  finalize(): void | Promise<void>;
}
