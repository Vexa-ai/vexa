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
export { createZoomSpeakers } from './zoom-speakers';
export type { ZoomSpeakers } from './zoom-speakers';
export { createZoomChat } from './zoom-chat';
export type { ZoomChat, ZoomChatMessage } from './zoom-chat';
