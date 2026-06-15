/**
 * @vexa/speaker-attribution — resolve opaque speaker keys → participant names.
 *
 * IN  : separated-transcript.v1 (segments keyed by an opaque channel/cluster id)
 *       + capture.v1 name events (the hints: who the platform UI shows speaking)
 * OUT : transcript.v1 (the same segments, now carrying resolved names)
 *
 * One brick, two key-sources it must stay agnostic over (internal strategy,
 * NOT separate bricks):
 *   - channel id  (multistream / gmeet — channels reassign, so still opaque)
 *   - cluster id  (mixed / zoom·msteams — diarizer clusters)
 * Binding strategy: window-match (overlap of hint turn vs segment) then
 * cluster-vote (majority name previously resolved for that key).
 *
 * Today: caption-boundary mapper (`speaker-mapper.ts`, here). The bot's
 * `cluster-name-binder.ts` (diarizer-cluster binding) folds in next — same
 * contract-out, so it is this brick's second strategy, not a new module.
 */
// Multistream / caption strategy (channel id → name via caption boundaries).
export { mapWordsToSpeakers, captionsToSpeakerBoundaries } from "./speaker-mapper";
export type {
  TimestampedWord,
  SpeakerBoundary,
  AttributedSegment,
  CaptionEvent,
} from "./speaker-mapper";
// Mixed strategy (diarizer cluster id → name via active-speaker hints).
export { attributeMixed } from "./mixed-attribution";
export type { MixedAttributionOptions } from "./mixed-attribution";
// Streaming counterpart of attributeMixed — names opaque segments live as they
// arrive (gmeet glow boundaries / any active-speaker hints), UNKNOWN-safe.
export { GlowAttribution } from "./glow-attribution";
export type { GlowAttributionOptions } from "./glow-attribution";
export { ClusterNameBinder } from "./cluster-name-binder";
export type { HintEvent, HintKind, CommitInfo, ResolvedAttribution } from "./cluster-name-binder";
export { setLogger } from "./log";
