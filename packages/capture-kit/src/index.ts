/**
 * @vexa/capture-kit — the isolated meeting-capture layer (browser context).
 *
 * Output of this brick = capture.v1 (per-speaker audio chunks + meeting events),
 * the input to the pipeline bricks. These modules run INSIDE the meeting page
 * (injected by the bot, or loaded by the extension) — zero node/back imports.
 * The host wires the emitted chunks/events to a capture.v1 sink (bot: in-process;
 * extension: WebSocket).
 */
export { createGmeetCapture } from "./gmeet-capture";
export { createGmeetSpeakers } from "./gmeet-speakers";
export { createZoomSpeakers } from "./zoom-speakers";
export {
  createTeamsSpeakers,
  teamsParticipantSelectors,
  teamsNameSelectors,
  teamsParticipantIdSelectors,
  teamsMeetingContainerSelectors,
} from "./msteams-speakers";
export { installRemoteAudioHook } from "./webrtc-audio-hook";
