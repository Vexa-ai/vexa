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
export { installRemoteAudioHook, observedPeerConnections } from './webrtc-audio-hook.js';
export type { WebRtcAudioHookOptions, ObservedPeerConnection } from './webrtc-audio-hook.js';
export {
  selectTeamsMixStreams, mainAudioProvedSilent,
  TEAMS_MAIN_AUDIO_GRACE_MS, TEAMS_MAIN_AUDIO_SILENCE_MS, TEAMS_MAIN_AUDIO_ENERGY_RMS,
} from './teams-main-audio.js';
export type {
  TeamsMixSelection, MainAudioAbsentObservation, MainAudioSilentObservation, StreamLike,
} from './teams-main-audio.js';
// The transport sensor: RTP contributing sources → active/inactive transitions (observation only).
export { createCsrcPoll, toEpochMs, CSRC_POLL_MS, CSRC_INACTIVE_MS } from './csrc-poll.js';
export type {
  CsrcPoll, CsrcPollOptions, CsrcPollHealth, CsrcTransition, CsrcPollErrorObservation,
  CsrcReceiverLike, ContributingSourceLike,
} from './csrc-poll.js';
