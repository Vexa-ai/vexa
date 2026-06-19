/**
 * AudioWorklet PCM capture — THE replacement for ScriptProcessorNode.
 *
 * ScriptProcessorNode runs its callback on the MAIN THREAD and, when the page is
 * busy (Meet rendering + several concurrent captures + STT round-trips), it
 * DUPLICATES or drops audio buffers. That doubled/overlapping PCM is exactly the
 * "stutter" heard in the captured audio (mic AND per-participant) — Whisper was
 * faithfully transcribing garbled input. An AudioWorklet runs on the dedicated
 * audio render thread, so it can't be starved by main-thread load.
 *
 * Contract-faithful: the ctx must already be at the target rate (16 kHz); this
 * emits fixed BLOCK-sized Float32 frames at that rate — same shape the old
 * ScriptProcessor(BLOCK, 1, 1) produced, so callers are a drop-in swap.
 */

const BLOCK = 4096;
/** The registered processor name — also exported so a host that ships the worklet
 *  as its own file (see WORKLET_SRC) keeps one source of truth for the name. */
export const PCM_WORKLET_PROCESSOR = 'vexa-pcm-capture';

// Worklet runs in its own realm — authored as a string. The body is the SINGLE
// SOURCE OF TRUTH for the processor; it is loaded one of two ways:
//   - moduleUrl given  → addModule(moduleUrl): the host ships THIS source as a
//     real file (a web_accessible_resource under MV3) and passes its URL via
//     chrome.runtime.getURL(...). REQUIRED under MV3's extension-page CSP, which
//     forbids `blob:` in script-src/worker-src ("Unable to load a worklet's
//     module") — the offscreen ch1000 mic path was silently blocked by it.
//   - no moduleUrl     → blob: URL fallback. Works in the bot / in-page MAIN
//     world (no extension CSP there), so those paths need no shipped file.
// Exported so the extension build can emit it verbatim as the shipped worklet.
export const WORKLET_SRC = `
class VexaPcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this._buf = new Float32Array(${BLOCK}); this._n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this._buf[this._n++] = ch[i];
      if (this._n === ${BLOCK}) { this.port.postMessage(this._buf); this._buf = new Float32Array(${BLOCK}); this._n = 0; }
    }
    return true;
  }
}
registerProcessor('${PCM_WORKLET_PROCESSOR}', VexaPcmCapture);
`;

// The processor name can only be registered ONCE per AudioContext — track which
// contexts already loaded the module (callers may create several nodes per ctx).
const moduled = new WeakSet<AudioContext>();

/**
 * Create an AudioWorklet capture node on `ctx` (which must be at 16 kHz). The
 * caller connects its source to the returned node and the node to
 * `ctx.destination` (to keep it pulled). `onPcm` receives a BLOCK-sized
 * Float32Array per frame — already a fresh copy, safe to forward without cloning.
 *
 * `moduleUrl` (optional) is the URL the worklet module is fetched from. Pass a
 * web_accessible_resource URL (chrome.runtime.getURL('vexa-pcm-worklet.js')) under
 * MV3, where the `blob:` fallback is CSP-blocked. Omit it (bot / in-page MAIN
 * world) to use the inline blob: URL. The module stays chrome-agnostic — the host
 * owns the URL.
 */
export async function createPcmCaptureNode(
  ctx: AudioContext,
  onPcm: (pcm: Float32Array) => void,
  moduleUrl?: string,
): Promise<AudioWorkletNode> {
  if (!moduled.has(ctx)) {
    if (moduleUrl) {
      await ctx.audioWorklet.addModule(moduleUrl);
    } else {
      const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: 'application/javascript' }));
      try { await ctx.audioWorklet.addModule(url); } finally { URL.revokeObjectURL(url); }
    }
    moduled.add(ctx);
  }
  // Force mono downmix (the old ScriptProcessor used 1 input channel).
  const node = new AudioWorkletNode(ctx, PCM_WORKLET_PROCESSOR, {
    numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
    channelCount: 1, channelCountMode: 'explicit', channelInterpretation: 'speakers',
  });
  node.port.onmessage = (e: MessageEvent) => onPcm(e.data as Float32Array);
  return node;
}
