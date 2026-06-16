/**
 * @vexa/capture — the isolated meeting-capture layer (browser context).
 *
 * Output of this brick = capture.v1 (per-speaker audio chunks + meeting events),
 * the input to the pipeline bricks. These modules run INSIDE the meeting page
 * (injected by the bot, or loaded by the extension) — zero node/back imports.
 * The host wires the emitted chunks/events to a capture.v1 sink (bot: in-process;
 * extension: WebSocket).
 */
// gmeet lane only — the mixed lane is carved out to @vexa/{mixed-capture-core,
// zoom-capture,teams-capture}; consumers import those directly. This package
// becomes @vexa/gmeet-capture when the gmeet lane is carved.
export { createPcmCaptureNode } from "./pcm-capture";
export { createGmeetCapture } from "./gmeet-capture";
export type { GmeetCapture } from "./gmeet-capture";
export { createGmeetSpeakers } from "./gmeet-speakers";
export type { GmeetSpeakers } from "./gmeet-speakers";
export { createGmeetCaptureV1, pickBoundName } from "./gmeet-capture-v1";
export type { GmeetCaptureV1, GmeetCaptureV1Options } from "./gmeet-capture-v1";
export { GmeetChannelBinder } from "./gmeet-channel-binder";
export type { GmeetChannelBinderOptions } from "./gmeet-channel-binder";
