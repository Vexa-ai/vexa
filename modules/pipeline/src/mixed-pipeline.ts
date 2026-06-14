/**
 * mixed-pipeline — the MIXED-topology strategy of the pipeline brick.
 *
 *   capture.v1 (one mixed channel + events)  ──►  separated-transcript.v1
 *                                                  (segments keyed by OPAQUE
 *                                                   diarization cluster id)
 *
 * A thin contract adapter over the ported ChunkedTranscriber (the single-pass
 * streaming core: gate-cut turns + wespeaker/online-clustering + LocalAgreement-2
 * Whisper confirmation). Its job is purely to map the bot's publish() callback
 * onto separated-transcript.v1's SeparatedTranscriptSink.
 *
 * CONTRACT BOUNDARY: this brick is identity-free. It emits the diarizer's
 * cluster id as `speakerKey` and NEVER resolves a name — name binding (the
 * ClusterNameBinder, fed by capture.v1 active-speaker hints) is the downstream
 * speaker-attribution brick. So we deliberately do NOT call recordHint here; the
 * hints stay in capture.v1 for the next brick. With no hints, ChunkedTranscriber's
 * resolveName() falls back to the cluster id — exactly what the contract wants.
 */
import { ChunkedTranscriber, type ChunkSegment } from './chunked-transcriber';
import type { TranscriptionResult } from './transcription-client';
import type { SeparatedSegment, SeparatedTranscriptSink } from './contracts/separated-transcript-v1';

export interface MixedPipelineOptions {
  /** One Whisper round-trip (stt.v1). Called serially by the core. */
  transcribe: (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;
  /** Where separated-transcript.v1 segments land (consumer = speaker-attribution). */
  sink: SeparatedTranscriptSink;
  /** Explicit language (skips Whisper's language gate). */
  language?: string;
  log?: (msg: string) => void;
}

export interface MixedPipeline {
  /** One mixed-audio frame, capture-time ms (capture.v1 audio ts). Streaming. */
  feedAudio(pcm: Float32Array, tsMs: number): void;
  /** Flush the open turn and finalize the sink. */
  dispose(): Promise<void>;
}

/** ChunkSegment (audio-ms) → SeparatedSegment (meeting-clock seconds). */
function toSegment(speakerKey: string, c: ChunkSegment): SeparatedSegment {
  return {
    speakerKey,                         // OPAQUE diarization cluster id — never a name
    text: c.text,
    start: c.startMs / 1000,
    end: c.endMs / 1000,
    words: [],                          // word timings: stt.v1 carries them; threaded in a later pass
    topology: 'mixed',
  };
}

export async function createMixedPipeline(opts: MixedPipelineOptions): Promise<MixedPipeline> {
  const tc = await ChunkedTranscriber.create({
    language: opts.language,
    transcribe: opts.transcribe,
    // No hints fed → `speaker` is the diarizer cluster id (resolveName fallback).
    publish: (speaker, confirmed, _pending) => {
      for (const c of confirmed) opts.sink.segment(toSegment(speaker, c));
    },
    publishPending: () => { /* pending is a live-UI affordance; the contract carries CONFIRMED segments only */ },
    clearPending: () => { /* no-op */ },
    rename: () => { /* no naming in the pipeline brick */ },
    log: opts.log,
  });
  return {
    feedAudio: (pcm, tsMs) => tc.feedAudio(pcm, tsMs),
    dispose: async () => { await tc.dispose(); await opts.sink.finalize(); },
  };
}
