/**
 * glow-attribution — the GLOW-EDGE strategy of speaker-attribution (Google Meet).
 *
 *   separated-transcript.v1 (speakerKey = OPAQUE glow-interval id)
 *     + capture.v1 active-speaker glow hints
 *        ──►  transcript.v1 (speakerKey resolved to a participant name)
 *
 * The streaming counterpart to attributeMixed (which is batch). The glow-pipeline
 * brick already segmented the audio AT the glow edges, so each opaque segment spans
 * exactly one glow turn; resolving its time window against the same glow hints
 * (ClusterNameBinder window-match) yields that turn's name with no channel
 * indirection. UNKNOWN-safe: a segment whose window has no confident hint match
 * stays the configured unknown label — never a stale or guessed name.
 *
 * Name cleaning (cleanName) is the single place messy UI tokens become display
 * names: corruption normalization / roster snap, and dropping non-participant
 * tokens (returns null → unknown). Host self-exclusion is upstream and STRUCTURAL
 * (capture's data-self-name) — not a name match here.
 */
import { ClusterNameBinder, type HintEvent } from './cluster-name-binder';
import type { SeparatedSegment } from './contracts/separated-transcript-v1';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1';

export interface GlowAttributionOptions {
  sink: TranscriptSink;
  /** Resolve a window-matched raw name → display name, or null to leave UNKNOWN.
   *  Default: identity (no cleaning). Inject a roster-snap here. */
  cleanName?: (raw: string) => string | null;
  /** Window-match tolerance (ms) for binding a segment to the glow lit during it. */
  matchToleranceMs?: number;
  /** Label for segments with no confident match. */
  unknownLabel?: string;
  log?: (msg: string) => void;
}

export class GlowAttribution {
  private binder: ClusterNameBinder;
  private clean: (raw: string) => string | null;
  private sink: TranscriptSink;
  private UNKNOWN: string;
  // speakerKey → name its live draft is currently shown under. The consumer keys
  // pending by RESOLVED name, so a draft that renames (UNKNOWN → name as the hint
  // lands) must clear the old name's pending first, or it orphans.
  private draftName = new Map<string, string>();

  constructor(o: GlowAttributionOptions) {
    this.binder = new ClusterNameBinder({ matchToleranceMs: o.matchToleranceMs ?? 800 });
    this.clean = o.cleanName ?? ((raw) => raw);
    this.sink = o.sink;
    this.UNKNOWN = o.unknownLabel ?? 'Speaker';
  }

  /** capture.v1 glow edge. tMs is epoch ms (same timebase as segment.start*1000). */
  recordHint(ev: HintEvent): void { this.binder.recordHint(ev); }

  private nameFor(seg: SeparatedSegment): { name: string | null; confidence: number } {
    const r = this.binder.resolve({ clusterId: seg.speakerKey, tStartMs: seg.start * 1000, tEndMs: seg.end * 1000 });
    // Only a window-match (a hint lit DURING the interval) names it — no cluster-vote
    // / provisional fallback to a stale name.
    const name = r.source === 'window-match' ? this.clean(r.speakerName) : null;
    return { name, confidence: name ? r.confidence : 0 };
  }

  private emitClear(speaker: string, seg: SeparatedSegment): void {
    this.sink.draft?.({ speaker, speakerKey: seg.speakerKey, text: '', start: seg.start, end: seg.end, words: [], source: 'provisional-cluster-id', confidence: 0, topology: seg.topology });
  }

  /** One CONFIRMED opaque glow-interval segment from the pipeline (separated-transcript.v1). */
  segment(seg: SeparatedSegment): void {
    const prev = this.draftName.get(seg.speakerKey);          // confirm supersedes any live draft for this key
    if (prev !== undefined) { this.draftName.delete(seg.speakerKey); this.emitClear(prev, seg); }
    const { name, confidence } = this.nameFor(seg);
    this.sink.segment({
      speaker: name ?? this.UNKNOWN, speakerKey: seg.speakerKey, text: seg.text,
      start: seg.start, end: seg.end, words: seg.words,
      source: name ? 'window-match' : 'provisional-cluster-id', confidence,
      topology: seg.topology,
    });
  }

  /** One LIVE PARTIAL (draft) for the same opaque key; empty text clears it. */
  draft(seg: SeparatedSegment): void {
    const prev = this.draftName.get(seg.speakerKey);
    if (!seg.text.trim()) {                                   // clear under whatever name it last showed
      if (prev !== undefined) { this.draftName.delete(seg.speakerKey); this.emitClear(prev, seg); }
      return;
    }
    const { name } = this.nameFor(seg);
    const speaker = name ?? this.UNKNOWN;
    if (prev !== undefined && prev !== speaker) this.emitClear(prev, seg);   // draft renamed → drop the old name's pending
    this.draftName.set(seg.speakerKey, speaker);
    this.sink.draft?.({
      speaker, speakerKey: seg.speakerKey, text: seg.text,
      start: seg.start, end: seg.end, words: seg.words,
      source: name ? 'window-match' : 'provisional-cluster-id', confidence: 0, topology: seg.topology,
    });
  }

  finalize(): void | Promise<void> { return this.sink.finalize(); }
}
