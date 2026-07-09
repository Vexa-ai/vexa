/**
 * @vexa/mixed-pipeline — the MIXED lane (Zoom / Teams / any single mixed stream).
 *
 *   mixed-capture.v1 (audio + hints) ─► ChunkedTranscriber
 *        ├─ PyannoteSegmenter        cut-only (the only ONNX; no diarization)
 *        ├─ shared/buffer            LocalAgreement-2 confirm
 *        ├─ shared/whisper           stt.v1 transcribe (injected)
 *        └─ ClusterNameBinder        the namer — hints by time window, no clustering
 *   ─► transcript.v1 (named segments + drafts via the publish/pending sink)
 *
 * Names come purely from time-windowed hints (`recordHint`); the per-turn
 * segmentation id is the key. There is NO speaker clustering.
 */
export { ChunkedTranscriber } from './chunked-transcriber.js';
export type { ChunkedTranscriberCallbacks, ChunkSegment, BoundarySource } from './chunked-transcriber.js';
export { PyannoteSegmenter } from './pyannote-segmenter.js';
export type { BoundaryEvent, PyannoteSegmenterConfig } from './pyannote-segmenter.js';
export { ClusterNameBinder } from './cluster-name-binder.js';
export type { HintKind, HintEvent } from './cluster-name-binder.js';
