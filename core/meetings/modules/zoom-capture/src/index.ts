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
 *   - createTrackNameResolver: on the PER-TRACK lane (Zoom's audio arrives as one WebRTC track per
 *     participant, so mixed-capture-core's mix is bypassed), the pure channel↔name correlator that
 *     turns those flaky active-speaker names into a stable binding per track. No DOM, no audio —
 *     golden-testable offline, like GmeetChannelBinder and TrackNamer.
 */
export { createZoomSpeakers } from './zoom-speakers.js';
export type { ZoomSpeakers } from './zoom-speakers.js';
export { createZoomChat } from './zoom-chat.js';
export type { ZoomChat, ZoomChatMessage } from './zoom-chat.js';
export { createTrackNameResolver, speakerLabel, TRACK_NAME_DEFAULTS } from './track-name-resolver.js';
export type { TrackNameResolver, TrackNameResolverOptions } from './track-name-resolver.js';
