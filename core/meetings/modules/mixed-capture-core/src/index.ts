/**
 * @vexa/mixed-capture-core — the platform-agnostic mixed-audio capture used by
 * every mixed-lane platform (Zoom, Teams, arbitrary tab). One mixed PCM stream
 * + the WebRTC remote-audio hook; no per-speaker channels, no names (those come
 * from the platform hint watchers in @vexa/zoom-capture / @vexa/teams-capture).
 *
 * Recording (the meeting mix → recording.v1) is a separate, platform-agnostic
 * concern — see `@vexa/record-chunker` (createRecordingTap), not here.
 */
export { createMixedAudioCapture } from './mixed-audio.js';
export type { MixedAudioCapture, MixedAudioOptions } from './mixed-audio.js';
export { installRemoteAudioHook } from './webrtc-audio-hook.js';
