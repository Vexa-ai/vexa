/**
 * Mixed-audio capture — capture a MIXED audio MediaStream (e.g. a tabCapture
 * stream for Zoom / Teams web) into 16 kHz PCM AND re-play it to the speakers so
 * the user still hears the meeting.
 *
 * THE bug: `getUserMedia({chromeMediaSource:'tab'})` MUTES the tab's own playback.
 * Constraints learned the hard way in the extension OFFSCREEN document:
 *   - the AudioWorklet (createPcmCaptureNode) loads from a `blob:` URL, which the
 *     offscreen's MV3 extension-page CSP blocks ("Unable to load a worklet's
 *     module"), and MV3 forbids `blob:` in both script-src AND worker-src. So
 *     capture here uses a ScriptProcessorNode — no module to load, no CSP issue.
 *     The offscreen is a dedicated, low-load document, so the main-thread stutter
 *     that retired ScriptProcessor in the busy meeting page does not apply.
 *   - a 16 kHz context's OUTPUT won't render on most devices → re-play through it
 *     is silent; and two AudioContexts on one tab track starve each other. So
 *     RE-PLAY uses a separate NATIVE-rate context on a CLONED track.
 *
 * Pure browser code (no node). Consumed by the extension's offscreen document and
 * available to the bot — same contract as the other capture bricks.
 */

export interface MixedAudioCapture {
  /** Stop re-play + capture and release resources. */
  stop(): void;
}

export interface MixedAudioOptions {
  /** PCM target rate (Hz). Default 16000. */
  sampleRate?: number;
  /** Skip near-silent frames below this peak amplitude. Default 0.005. */
  silenceThreshold?: number;
  /** Re-play the stream to the speakers (un-mute the captured tab). Default true. */
  replay?: boolean;
  log?: (msg: string) => void;
}

export async function createMixedAudioCapture(
  stream: MediaStream,
  onPcm: (pcm: Float32Array) => void,
  opts: MixedAudioOptions = {},
): Promise<MixedAudioCapture> {
  const SR = opts.sampleRate ?? 16000;
  const SILENCE = opts.silenceThreshold ?? 0.005;
  const log = opts.log ?? (() => { /* silent */ });

  // ── CAPTURE — 16 kHz context + ScriptProcessor (no worklet module → no CSP) ────
  const ctx = new AudioContext({ sampleRate: SR });
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = (e: AudioProcessingEvent) => {
    const input = e.inputBuffer.getChannelData(0);
    let maxVal = 0;
    for (let i = 0; i < input.length; i++) { const a = Math.abs(input[i]); if (a > maxVal) maxVal = a; }
    if (maxVal > SILENCE) onPcm(new Float32Array(input));   // copy — the buffer is reused
  };
  source.connect(proc);
  proc.connect(ctx.destination);                           // pull the processor (outputs silence)
  void ctx.resume().catch(() => { /* best-effort — never await in an offscreen */ });
  log(`pcm capture @ ${SR} Hz (ScriptProcessor)`);

  // ── RE-PLAY — native context (no worklet) on a CLONED track ───────────────────
  let playCtx: AudioContext | null = null;
  let cloned: MediaStreamTrack | null = null;
  if (opts.replay !== false) {
    const track = stream.getAudioTracks()[0];
    cloned = track ? track.clone() : null;
    if (cloned) {
      playCtx = new AudioContext();                        // device-native rate
      playCtx.createMediaStreamSource(new MediaStream([cloned])).connect(playCtx.destination);
      void playCtx.resume().catch(() => { /* best-effort */ });
      log(`re-play @ ${playCtx.sampleRate} Hz (native, cloned track)`);
    }
  }

  return {
    stop(): void {
      try { proc.disconnect(); proc.onaudioprocess = null; } catch { /* */ }
      try { playCtx?.close(); } catch { /* */ }
      try { cloned?.stop(); } catch { /* */ }
      try { ctx.close(); } catch { /* */ }
    },
  };
}
