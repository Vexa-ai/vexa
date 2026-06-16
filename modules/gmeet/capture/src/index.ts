/**
 * @vexa/gmeet-capture — the Google Meet TRANSCRIPTION capture layer (browser).
 *
 * Per-channel audio + glow active-speaker → gmeet-capture.v1 (the name bound
 * onto each channel at the source). Runs INSIDE the meeting page (injected by
 * the bot, or loaded by the extension) — zero node/back imports; the host wires
 * the emitted frames to a capture.v1 sink (bot: in-process; extension: WebSocket).
 *
 * Recording (the combined meeting mix → recording.v1) is a separate, platform-
 * agnostic concern — see `@vexa/record-chunker` (createRecordingTap), not here.
 */
export { createPcmCaptureNode } from "./pcm-capture";
export { createGmeetCapture } from "./gmeet-capture";
export type { GmeetCapture } from "./gmeet-capture";
export { createGmeetSpeakers } from "./gmeet-speakers";
export type { GmeetSpeakers } from "./gmeet-speakers";
export { createGmeetCaptureV1, pickBoundName } from "./gmeet-capture-v1";
export type { GmeetCaptureV1, GmeetCaptureV1Options } from "./gmeet-capture-v1";
export { GmeetChannelBinder } from "./gmeet-channel-binder";
export type { GmeetChannelBinderOptions } from "./gmeet-channel-binder";
