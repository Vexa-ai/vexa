/**
 * mixed-attribution — the MIXED-topology strategy of speaker-attribution.
 *
 *   separated-transcript.v1 (speakerKey = diarizer cluster id)
 *     + capture.v1 active-speaker hints
 *        ──►  transcript.v1 (speakerKey resolved to a participant name)
 *
 * The counterpart to speaker-mapper.ts (the multistream/caption strategy). Both
 * emit transcript.v1; this one binds diarizer CLUSTERS to names via the ported
 * ClusterNameBinder (window-overlap of a cluster's segment span against the
 * lit-hint turns, then cluster-vote majority, else the cluster id provisionally).
 *
 * Two passes: pass 1 resolves every segment (accumulating per-cluster name
 * votes); pass 2 re-resolves the still-provisional segments now that all votes
 * are in — so a cluster named only late retroactively names its earlier turns
 * (the batch equivalent of the binder's onLateResolve rename).
 */
import { ClusterNameBinder, type HintEvent } from './cluster-name-binder';
import type { SeparatedSegment } from './contracts/separated-transcript-v1';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1';

export interface MixedAttributionOptions {
  /** capture.v1 active-speaker name events. tMs is epoch ms (same timebase as segment.start*1000). */
  hints: HintEvent[];
  sink: TranscriptSink;
  log?: (msg: string) => void;
}

export function attributeMixed(segments: SeparatedSegment[], opts: MixedAttributionOptions): void {
  const binder = new ClusterNameBinder({});
  for (const h of opts.hints) binder.recordHint(h);

  // Pass 1 — resolve in time order; window-matches feed the cluster-vote history.
  const resolved = segments.map((seg) => {
    const r = binder.resolve({ clusterId: seg.speakerKey, tStartMs: seg.start * 1000, tEndMs: seg.end * 1000 });
    return { seg, speaker: r.speakerName, source: r.source, confidence: r.confidence };
  });

  // Pass 2 — provisional clusters may now have a majority name.
  let upgraded = 0;
  for (const r of resolved) {
    if (r.source !== 'provisional-cluster-id') continue;
    const re = binder.resolve({ clusterId: r.seg.speakerKey, tStartMs: r.seg.start * 1000, tEndMs: r.seg.end * 1000 });
    if (re.source !== 'provisional-cluster-id') { r.speaker = re.speakerName; r.source = re.source; r.confidence = re.confidence; upgraded++; }
  }
  if (upgraded) opts.log?.(`pass 2 upgraded ${upgraded} provisional segment(s) via cluster-vote`);

  for (const r of resolved) {
    const out: TranscriptSegment = {
      speaker: r.speaker,
      speakerKey: r.seg.speakerKey,
      text: r.seg.text,
      start: r.seg.start,
      end: r.seg.end,
      words: r.seg.words,
      source: r.source,
      confidence: r.confidence,
      topology: r.seg.topology,
    };
    opts.sink.segment(out);
  }
  void opts.sink.finalize();
}
