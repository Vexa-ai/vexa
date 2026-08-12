/**
 * @vexa/mixed-pipeline — the MIXED lane (Zoom / Teams / any single mixed stream).
 *
 *   mixed-capture.v1 (audio + hints + transport edges) ─► ChunkedTranscriber
 *        ├─ TurnSource               WHERE the turn edges come from:
 *        │    ├─ PyannoteTurnSource    the segmentation model, inferred from the waveform
 *        │    └─ CsrcTurnSource        the RTP mixer's own account of who is audible
 *        ├─ shared/buffer            LocalAgreement-2 confirm
 *        ├─ shared/whisper           stt.v1 transcribe (injected)
 *        ├─ TrackNamer               WHO a transport track is — earned once per meeting
 *        └─ ClusterNameBinder        the pyannote lane's namer — hints by time window
 *   ─► transcript.v1 (named segments + drafts via the publish/pending sink)
 *
 * On the pyannote spine, names come purely from time-windowed hints (`recordHint`) and the
 * per-turn segmentation id is the key. On the transport spine the turn carries a stable track id
 * and the name is resolved per TRACK, not per turn. There is NO speaker clustering on either.
 */
export { ChunkedTranscriber } from './chunked-transcriber.js';
export type { ChunkedTranscriberCallbacks, ChunkSegment, BoundarySource, TurnSourceObservation, LaneClock } from './chunked-transcriber.js';
export { PyannoteTurnSource, CsrcTurnSource, CSRC_HYSTERESIS_MS, CSRC_DEATH_MS } from './turn-source.js';
export type {
  TurnSource, TurnSourceCallbacks, TurnSourceHealth, TransportEvent, TurnCloseReason,
  TurnOpenedEvent, TurnGrownEvent, TurnClosedEvent,
} from './turn-source.js';
export { VirtualClock } from './virtual-clock.js';
export type { VirtualClockOptions } from './virtual-clock.js';
export { correlateSourcesToNames } from './source-name-correlator.js';
export type { Interval, Binding, CorrelationResult, CorrelatorOptions } from './source-name-correlator.js';
export { TrackNamer, speakerLabel } from './track-namer.js';
export type { TrackNaming, TrackEvidence, TrackNamerOptions, TrackNamerObservation } from './track-namer.js';
export { PyannoteSegmenter } from './pyannote-segmenter.js';
export type { BoundaryEvent, PyannoteSegmenterConfig } from './pyannote-segmenter.js';
export { ClusterNameBinder } from './cluster-name-binder.js';
export { hallucinationRule } from './hallucination-gate.js';
export type { HallucinationRule, SuppressedSegment } from './hallucination-gate.js';
export type { HintKind, HintEvent } from './cluster-name-binder.js';
