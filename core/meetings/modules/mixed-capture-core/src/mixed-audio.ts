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

/** Delivery accounting — see `stats()`. Every number is seconds of audio except the counts. */
export interface MixedAudioStats {
  /** Buffers the ScriptProcessor handed us. */
  seen: number;
  /** Buffers that passed the silence gate. */
  emitted: number;
  /** `seen` as audio time — what capture actually received. */
  deliveredSec: number;
  /** The AudioContext's own elapsed time — what the graph rendered. */
  renderedSec: number;
  /** `renderedSec - deliveredSec`: audio that existed and never reached the callback. */
  processorDeficitSec: number;
  /** `seen - emitted` as audio time: audio the gate refused. */
  gatedSec: number;
}

export interface MixedAudioCapture {
  /** Stop re-play + capture and release resources. */
  stop(): void;
  /**
   * Where the audio went. A frame missing downstream was either never delivered by the
   * ScriptProcessor or refused by the silence gate, and the two have entirely different fixes —
   * so they are counted separately against the context clock, which is the only local witness to
   * how much audio existed. Without this split a missing 256 ms is unattributable.
   */
  stats(): MixedAudioStats;
}

export interface MixedAudioOptions {
  /** PCM target rate (Hz). Default 16000. */
  sampleRate?: number;
  /**
   * Skip frames whose peak amplitude is at or below this. **Default 0 — nothing is skipped.**
   *
   * Dropping a frame does not drop audio, it drops TIME, and everything downstream reconstructs
   * meaning from timestamps: the segmenter splices speech onto speech across the hole and reads it
   * as a speaker change, turn boundaries land where no audio was delivered, and the hint binder
   * matches names against a clock that has drifted. Measured on a real bot renderer, this gate
   * discarded 85s of a 128s session while the ScriptProcessor delivered every buffer it was given —
   * so the gate was the entire capture deficit.
   *
   * The cost it was paying for is already paid downstream, twice: the pipeline refuses to submit a
   * span below DROP_RMS, and drops whatever comes back through the post-Whisper acoustic gates.
   * Silence never reached STT because of this; it reached it because of those.
   */
  silenceThreshold?: number;
  /** Re-play the stream to the speakers (un-mute the captured tab). Default true. */
  replay?: boolean;
  log?: (msg: string) => void;
}

/** ScriptProcessor buffer size — 4096 @ 16 kHz = 256 ms per frame. */
const BUFFER_SAMPLES = 4096;
/** Log the delivery split every this many buffers (100 ≈ 25 s of audio). */
const REPORT_EVERY = 100;


export async function createMixedAudioCapture(
  stream: MediaStream,
  onPcm: (pcm: Float32Array) => void,
  opts: MixedAudioOptions = {},
): Promise<MixedAudioCapture> {
  const SR = opts.sampleRate ?? 16000;
  const SILENCE = opts.silenceThreshold ?? 0;
  const log = opts.log ?? (() => { /* silent */ });

  // ── CAPTURE — 16 kHz context + ScriptProcessor (no worklet module → no CSP) ────
  const ctx = new AudioContext({ sampleRate: SR });
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(BUFFER_SAMPLES, 1, 1);

  // Samples, not buffer counts: the engine chooses the buffer length, so counting what actually
  // arrived is the only figure that stays true if it ever chooses differently.
  let seen = 0, emitted = 0, deliveredSamples = 0, gatedSamples = 0, ctxStart = -1;
  const stats = (): MixedAudioStats => {
    const deliveredSec = deliveredSamples / SR;
    const renderedSec = ctxStart < 0 ? 0 : ctx.currentTime - ctxStart;
    return {
      seen, emitted, deliveredSec, renderedSec,
      processorDeficitSec: Math.max(0, renderedSec - deliveredSec),
      gatedSec: gatedSamples / SR,
    };
  };

  proc.onaudioprocess = (e: AudioProcessingEvent) => {
    const input = e.inputBuffer.getChannelData(0);
    // The context clock at the FIRST buffer is the zero, less that buffer's own span: everything
    // before it is graph startup, not loss.
    if (ctxStart < 0) ctxStart = ctx.currentTime - input.length / SR;
    seen++;
    deliveredSamples += input.length;
    let maxVal = 0;
    for (let i = 0; i < input.length; i++) { const a = Math.abs(input[i]); if (a > maxVal) maxVal = a; }
    // A NON-POSITIVE threshold disables gating outright, digital silence included. Testing
    // `maxVal > 0` would still drop an all-zero buffer — and an all-zero buffer is precisely what a
    // codec's silence suppression emits, so that is the case a threshold of zero must let through.
    if (SILENCE <= 0 || maxVal > SILENCE) { emitted++; onPcm(new Float32Array(input)); }   // copy — the buffer is reused
    else gatedSamples += input.length;
    if (seen % REPORT_EVERY === 0) {
      const s = stats();
      log(`capture: seen=${s.seen} emitted=${s.emitted} · delivered ${s.deliveredSec.toFixed(1)}s of ` +
        `${s.renderedSec.toFixed(1)}s rendered · processor deficit ${s.processorDeficitSec.toFixed(1)}s · ` +
        `gated ${s.gatedSec.toFixed(1)}s`);
    }
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
    stats,
  };
}
