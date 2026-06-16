/**
 * mixed-capture.v1 — the capture→pipeline contract for the MIXED lane
 * (Zoom / Teams / any single mixed stream). Two routable streams:
 *
 *   AUDIO  frame = { ts: float(epoch ms), pcm: Float32[] }   — one mixed channel
 *   HINT         = { name: string, ts: float, isEnd: bool }  — "who's lit" by time
 *                  (kind: 'dom-active' Zoom | 'dom-outline' Teams | 'caption')
 *
 * The audio has NO speaker name and NO per-speaker channels (contrast
 * gmeet-capture.v1, where the glow name rides on each frame). Names are resolved
 * DOWNSTREAM in @vexa/mixed-pipeline by window-matching hints against the
 * pyannote-segmentation turns. Wire serialization is @vexa/capture-codec
 * (audio = the no-name binary frame; hint = an `active-speaker` MeetingEvent).
 *
 * Producers: @vexa/mixed-capture-core (audio) + @vexa/zoom-capture /
 * @vexa/teams-capture (hints). Consumer: @vexa/mixed-pipeline.
 */
import type { MeetingEvent } from '@vexa/capture-codec';

/** Hint lag/source — calibrated per platform in the mixed-pipeline namer. */
export type HintKind = 'dom-active' | 'dom-outline' | 'caption';

/** One mixed-audio chunk. `track` is always the mixed channel (no identity). */
export interface MixedFrame {
  ts: number;               // CAPTURE epoch ms — stamped at the source, never restamped
  pcm: Float32Array;        // 16 kHz mono PCM
}

/** "Who is lit" at a wall-clock instant. A new hint of the same kind ends the
 *  previous one; `isEnd` closes the current speaker's turn explicitly. */
export interface MixedHint {
  name: string;             // platform display name of the active speaker ('' with isEnd = ended)
  ts: number;               // CAPTURE epoch ms the signal was observed
  isEnd: boolean;           // explicit turn end (no new speaker yet)
  kind: HintKind;
}

/** A MixedHint as it rides @vexa/capture-codec's event envelope on the wire. */
export function hintToEvent(h: MixedHint): MeetingEvent {
  return { kind: 'active-speaker', ts: h.ts, speaker: h.name, detail: { hint: h.kind, isEnd: h.isEnd } };
}

/** Recover a MixedHint from a decoded `active-speaker` event (or null if it isn't one). */
export function hintFromEvent(ev: MeetingEvent): MixedHint | null {
  if (ev.kind !== 'active-speaker') return null;
  const d = (ev.detail || {}) as { hint?: HintKind; isEnd?: boolean };
  return { name: ev.speaker || '', ts: ev.ts, isEnd: !!d.isEnd, kind: d.hint || 'dom-active' };
}
