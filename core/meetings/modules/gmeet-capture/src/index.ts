/**
 * @vexa/gmeet-capture — the Google Meet capture layer (browser).
 *
 * Per-channel audio + the glow active-speaker → capture.v1 (the name bound onto each channel at the
 * source). Runs INSIDE the meeting page (injected by the bot, or loaded by the extension) — zero
 * node/back imports; the host wires the emitted frames to a capture.v1 sink (bot: in-process;
 * extension: WebSocket via @vexa/capture-codec). The capture.v1 model + wire codec is @vexa/capture-codec.
 */
export { createPcmCaptureNode, WORKLET_SRC, PCM_WORKLET_PROCESSOR } from "./pcm-capture.js";
export { createGmeetCapture } from "./gmeet-capture.js";
export type { GmeetCapture } from "./gmeet-capture.js";
export { createGmeetSpeakers } from "./gmeet-speakers.js";
export type { GmeetSpeakers } from "./gmeet-speakers.js";
export { createGmeetCaptureV1, pickBoundName } from "./gmeet-capture-v1.js";
export type { GmeetCaptureV1, GmeetCaptureV1Options } from "./gmeet-capture-v1.js";
export { GmeetChannelBinder } from "./gmeet-channel-binder.js";
export type { GmeetChannelBinderOptions } from "./gmeet-channel-binder.js";
