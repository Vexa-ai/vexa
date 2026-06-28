/**
 * gmeet-capture-v1 — the Google Meet capture.v1 PRODUCER (browser context).
 *
 * Composes the two raw-signal bricks of this module into ONE capture.v1 stream:
 *   - gmeet-capture   → per-participant PCM chunks (onAudio(index, pcm))
 *   - gmeet-speakers  → the live glow (litNames(): who is lit RIGHT NOW)
 *
 * and STAMPS each audio chunk with the glow name lit at that chunk's capture time.
 * This binds identity to AUDIO at the source — the inversion the gmeet rethink
 * needs: Meet's remote channels are an anonymous rotating pool (channel ≠ speaker),
 * but the glow names the live speaker, so the only reliable key is the lit name,
 * not the channel index. Binding here (audio, not a transcript segment) keeps SoC
 * clean and means the downstream gmeet pipeline transcribes PER-NAME with no
 * diarizer and no post-hoc window-match.
 *
 * Pure browser code — no node/Playwright. Used by BOTH services: the bot wires
 * `sink` to its in-process CaptureV1Sink; the extension wires `sink` to the WS
 * codec (encodeAudioFrame now carries speakerName). Same module, two services.
 */
import { createGmeetCapture, type GmeetCaptureOptions } from './gmeet-capture';
import { createGmeetSpeakers } from './gmeet-speakers';
import type { CaptureV1Sink } from './contract/capture-v1';

export interface GmeetCaptureV1Options {
  /** capture.v1 out — bot: in-process sink; extension: WS-encoding sink. */
  sink: CaptureV1Sink;
  /** Local participant display name — excluded from glow candidates. */
  selfName?: string;
  /** Capture clock (epoch ms), set at the source. Default Date.now. Injectable for tests. */
  now?: () => number;
  /** Glow poll interval (ms). Default = gmeet-speakers default (250). */
  pollMs?: number;
  /** Passthrough tuning for the audio capture (onAudio is supplied internally). */
  capture?: Omit<GmeetCaptureOptions, 'onAudio'>;
  log?: (msg: string) => void;
}

export interface GmeetCaptureV1 {
  start(): Promise<void>;
  stop(): void;
  /** Number of currently-connected participant streams. */
  streamCount(): number;
}

/**
 * The binding decision, kept pure + exported so it is golden-testable without a DOM.
 * EXACTLY ONE tile lit ⇒ that name (the live speaker). Zero lit ⇒ undefined
 * (silence/settling → resolve or UNKNOWN downstream). Two+ lit ⇒ undefined
 * (overlap is ambiguous at the source — never guess a name). Honest by design:
 * the binder emits a name only when the glow is unambiguous.
 */
export function pickBoundName(litNames: string[]): string | undefined {
  return litNames.length === 1 ? litNames[0] : undefined;
}

export function createGmeetCaptureV1(opts: GmeetCaptureV1Options): GmeetCaptureV1 {
  const now = opts.now ?? (() => Date.now());

  // Glow watcher: drives both the per-chunk binding (litNames) AND the legacy
  // active-speaker hint stream on capture.v1 (kept for audit + the mixed lane).
  const speakers = createGmeetSpeakers({
    selfName: opts.selfName,
    pollMs: opts.pollMs,
    log: opts.log,
    onSpeaking: (name, isEnd) =>
      opts.sink.event({ kind: 'active-speaker', ts: now(), speaker: name, detail: { hint: 'dom-active', isEnd } }),
  });

  // Per-participant audio: each chunk is stamped with the lit glow name at its ts.
  const capture = createGmeetCapture({
    ...opts.capture,
    log: opts.log,
    onAudio: (index, pcm) => {
      const speakerName = pickBoundName(speakers.litNames());
      opts.sink.audioChunk({ speakerId: `spk-${index}`, speakerIndex: index, samples: pcm, ts: now(), speakerName });
    },
  });

  return {
    start: () => capture.start(),
    stop: () => { capture.stop(); speakers.destroy(); },
    streamCount: () => capture.streamCount(),
  };
}
