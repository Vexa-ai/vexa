/**
 * @vexa/zoom-capture — Zoom's contribution to the mixed lane.
 *
 * Zoom mixes all participants into one audio stream (captured by
 * @vexa/mixed-capture-core), so this module provides only the WHO signal:
 *   - createZoomSpeakers: polls Zoom's active-speaker DOM (~250ms) and emits a
 *     name change on each transition → a mixed-capture.v1 `hint`
 *     ({ name, ts, isEnd }, kind 'dom-active'). The downstream @vexa/mixed-pipeline
 *     namer window-matches these against segmentation turns.
 *   - createZoomChat: reads the chat panel (content tier).
 */
export { createZoomSpeakers } from './zoom-speakers.js';
export type {
  ZoomNameUnresolvedObservation,
  ZoomSpeakers,
} from './zoom-speakers.js';
export {
  parseZoomProducerDomTrace,
  ZOOM_TRACE_CONFIRM_POLLS,
  ZOOM_TRACE_FOOTERS,
  ZOOM_TRACE_MAX_ROWS,
  ZOOM_TRACE_POLL_MS,
  ZOOM_TRACE_PSEUDONYMS,
  ZOOM_TRACE_SCHEMA,
  ZOOM_TRACE_VIEWS,
  ZoomProducerDomTraceAdmissionError,
} from './producer-dom-trace.js';
export type {
  ZoomProducerDomTrace,
  ZoomProducerDomTraceAdmissionCode,
  ZoomProducerDomTraceHeader,
  ZoomProducerDomTraceRow,
  ZoomTraceFooter,
  ZoomTracePseudonym,
  ZoomTraceView,
} from './producer-dom-trace.js';
export { createZoomChat } from './zoom-chat.js';
export type { ZoomChat, ZoomChatMessage } from './zoom-chat.js';
