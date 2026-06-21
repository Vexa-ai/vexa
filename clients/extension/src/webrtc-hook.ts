/**
 * DISABLED — the WebRTC remote-audio hook mirrored each remote participant's
 * track into a hidden <audio> for per-participant capture on Zoom/Teams. We do
 * NOT use that: Zoom can't expose per-participant audio (WASM) and Teams MUST
 * follow Zoom's mixed path exactly (one diarized tab-audio channel, 999). Leaving
 * the hook on would (a) re-introduce per-participant channels for Teams and
 * (b) double Teams audio (the mirrored <audio> elements play the remote track a
 * second time). Per-participant capture is Google Meet only (native elements).
 *
 * Kept as a no-op content script so the manifest/build stays stable; the shared
 * `installRemoteAudioHook` still lives in @vexa/capture for the bot's own use.
 *
 * It is registered at document_start in the MAIN world (the slot a future
 * re-enable would need) for Zoom/Teams only, mirroring the shipped manifest.
 */
export {};
