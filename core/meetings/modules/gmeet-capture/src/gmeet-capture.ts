/**
 * Google Meet per-participant audio capture — THE shared implementation.
 *
 * Pure browser code (no Node, no Playwright). Consumed by BOTH:
 *  - the bot: bundled into browser-utils.global.js; index.ts installs it in-page
 *    and feeds onAudio → __vexaPerSpeakerAudioData (the Playwright bridge).
 *  - the extension: imported by inpage.ts; onAudio → postMessage to the WS.
 *
 * Google Meet renders each participant's audio as a separate <audio>/<video>
 * element whose srcObject is a live MediaStream. This wires each into a
 * dedicated AudioContext → AudioWorklet, resampled to 16 kHz, and delivers
 * per-element PCM chunks via onAudio(index, pcm). It rescans for late joiners /
 * recycled elements and silence-gates each chunk. The track index is stable per
 * stream id (the basis for per-track speaker attribution in gmeet-speakers.ts).
 */

import { createPcmCaptureNode } from './pcm-capture.js';

export interface GmeetCaptureOptions {
  /** One per-element PCM chunk (already 16 kHz). index is the stable track index. */
  onAudio: (index: number, pcm: Float32Array) => void;
  log?: (msg: string) => void;
  targetSampleRate?: number;   // default 16000
  bufferSize?: number;         // default 4096
  silenceThreshold?: number;   // default 0.005 — skip near-silent chunks
  silenceHangoverMs?: number;  // default 2000 — preserve bounded inter-phrase pauses
  rescanMs?: number;           // default 15000 — discover late joiners
  findRetries?: number;        // default 10
  findDelayMs?: number;        // default 2000
}

export interface GmeetCapture {
  start(): Promise<void>;
  stop(): void;
  /** Number of currently-connected participant streams. */
  streamCount(): number;
}

export interface SilenceGateState {
  emit: boolean;
  remainingSamples: number;
}

/**
 * Advance the per-stream silence gate.
 *
 * Speech refreshes a bounded sample allowance. Following silent frames consume
 * that allowance and stay in the same PCM timeline; once exhausted, idle audio
 * is gated again. Whole frames are indivisible, so the emitted hangover rounds
 * up to the next frame boundary.
 */
export function advanceSilenceGate(
  isLoud: boolean,
  frameSamples: number,
  remainingSamples: number,
  hangoverSamples: number,
): SilenceGateState {
  if (isLoud) {
    return { emit: true, remainingSamples: Math.max(0, hangoverSamples) };
  }
  if (remainingSamples <= 0) {
    return { emit: false, remainingSamples: 0 };
  }
  return {
    emit: true,
    remainingSamples: Math.max(0, remainingSamples - frameSamples),
  };
}

export function createGmeetCapture(opts: GmeetCaptureOptions): GmeetCapture {
  const log = opts.log || (() => { /* silent */ });
  const SR = opts.targetSampleRate ?? 16000;
  const SILENCE = opts.silenceThreshold ?? 0.005;
  const SILENCE_HANGOVER_SAMPLES = Math.max(0, Math.round(
    (opts.silenceHangoverMs ?? 2000) * SR / 1000,
  ));
  const RESCAN = opts.rescanMs ?? 15000;
  const FIND_RETRIES = opts.findRetries ?? 10;
  const FIND_DELAY = opts.findDelayMs ?? 2000;

  let running = false;
  let rescanTimer: ReturnType<typeof setInterval> | null = null;
  const connectedStreamIds = new Set<string>();
  const contexts: AudioContext[] = [];
  let nextIndex = 0;

  function findMediaElements(): HTMLMediaElement[] {
    return Array.from(document.querySelectorAll('audio, video')).filter((el: any) =>
      !el.paused &&
      el.srcObject instanceof MediaStream &&
      el.srcObject.getAudioTracks().length > 0
    ) as HTMLMediaElement[];
  }

  function connectElement(el: HTMLMediaElement, index: number): boolean {
    try {
      const stream: MediaStream = (el as any).srcObject;
      if (!stream || stream.getAudioTracks().length === 0) return false;
      const streamId = stream.id;
      if (connectedStreamIds.has(streamId)) return false;

      const ctx = new AudioContext({ sampleRate: SR });
      // Chrome's autoplay policy can create the context SUSPENDED (no user gesture) → the worklet
      // never runs → zero PCM even while people talk. Resume it explicitly. (L4 capture fix.)
      void ctx.resume().then(() => log(`stream ${index} ctx.state=${ctx.state}`)).catch(() => { /* */ });
      const source = ctx.createMediaStreamSource(stream);
      // AudioWorklet (audio-thread) instead of the deprecated ScriptProcessor,
      // which duplicates/drops buffers under main-thread load — the captured-audio
      // stutter. connectElement is sync, so wire the node when addModule resolves.
      let seen = 0, emitted = 0; // L4 frame-flow diagnostic
      let silenceSamplesRemaining = 0;
      createPcmCaptureNode(ctx, (data) => {
        if (!running) return;
        seen++;
        let maxVal = 0;
        for (let i = 0; i < data.length; i++) { const a = Math.abs(data[i]); if (a > maxVal) maxVal = a; }
        const gate = advanceSilenceGate(
          maxVal > SILENCE,
          data.length,
          silenceSamplesRemaining,
          SILENCE_HANGOVER_SAMPLES,
        );
        silenceSamplesRemaining = gate.remainingSamples;
        if (gate.emit) { emitted++; if (emitted === 1 || emitted % 100 === 0) log(`stream ${index} AUDIO seen=${seen} emitted=${emitted} max=${maxVal.toFixed(3)}`); opts.onAudio(index, data); } // worklet already yields a fresh copy
        else if (seen % 250 === 0) log(`stream ${index} silent seen=${seen} emitted=${emitted} max=${maxVal.toFixed(4)} ctx=${ctx.state}`);
      }).then((node) => { source.connect(node); node.connect(ctx.destination); })
        .catch((err: any) => console.log(`[gmeet-capture] worklet init failed: ${err?.message}`));
      connectedStreamIds.add(streamId);
      contexts.push(ctx);

      const track = stream.getAudioTracks()[0];
      track.addEventListener('ended', () => { connectedStreamIds.delete(streamId); });

      log(`stream ${index} connected (track ${track.id.substring(0, 8)})`);
      return true;
    } catch (err: any) {
      log(`stream ${index} error: ${err.message}`);
      return false;
    }
  }

  return {
    async start(): Promise<void> {
      if (running) return;
      running = true;

      let mediaElements: HTMLMediaElement[] = [];
      for (let attempt = 0; attempt < FIND_RETRIES && running; attempt++) {
        mediaElements = findMediaElements();
        if (mediaElements.length > 0) break;
        await new Promise(r => setTimeout(r, FIND_DELAY));
      }
      if (!running) return;

      for (let i = 0; i < mediaElements.length; i++) {
        if (connectElement(mediaElements[i], i)) nextIndex = i + 1;
      }
      nextIndex = Math.max(nextIndex, mediaElements.length);

      rescanTimer = setInterval(() => {
        if (!running) return;
        for (const el of findMediaElements()) {
          const stream: MediaStream = (el as any).srcObject;
          if (stream && !connectedStreamIds.has(stream.id)) {
            if (connectElement(el, nextIndex)) nextIndex++;
          }
        }
      }, RESCAN);

      log(`capture started with ${connectedStreamIds.size} stream(s)`);
    },

    stop(): void {
      running = false;
      if (rescanTimer !== null) { clearInterval(rescanTimer); rescanTimer = null; }
      for (const ctx of contexts) { try { ctx.close(); } catch { /* ignore */ } }
      contexts.length = 0;
      connectedStreamIds.clear();
      nextIndex = 0;
      log('capture stopped');
    },

    streamCount(): number { return connectedStreamIds.size; },
  };
}
