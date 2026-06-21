/**
 * Pipeline adapter (2b) — the capture → lane → STT → transcript.v1 engine, behind the
 * orchestrator's Pipeline port.
 *
 * The bot rewires NOTHING about transcription topology: the per-channel lane (turn gating,
 * LocalAgreement confirmation, glow→channel naming) lives in @vexa/gmeet-pipeline, the mixed
 * lane (pyannote cut + hints naming, no diarizer) in @vexa/mixed-pipeline, and the stt.v1
 * round-trip in @vexa/transcribe-whisper. This adapter only:
 *   1. picks the lane on inv.platform (google_meet → gmeet/per-channel; zoom/teams → mixed),
 *   2. injects the stt port (TranscriptionClient.transcribe) + a TranscriptSink, and
 *   3. RECONCILES the lane's TranscriptSink (segment/draft/finalize — owned by the lane's
 *      transcript.v1 contract) onto the bot's injected TranscriptSink.publish(segment) port.
 *
 * The capture BRIDGE (browser page-side capture → feedAudio) lives in capture-bridge.ts and is
 * L4-gated; this file is the offline-provable lane composition (L2/L3 via pipeline.test.ts:
 * synthetic PCM → mock transcribe → assert publish got transcript.v1-valid segments).
 *
 * Ported from services/vexa-bot_new/src/pipeline/gmeet-pipeline.ts (the lane wrapper) onto the
 * v0.12 ports; the mixed branch wires @vexa/mixed-pipeline's ChunkedTranscriber for Zoom/Teams.
 */
import {
  createGmeetPipeline,
  type TranscriptSink as LaneTranscriptSink,
  type TranscriptSegment as LaneSegment,
  type SpeakerStreamManagerConfig,
} from '@vexa/gmeet-pipeline';
import {
  ChunkedTranscriber,
  type ChunkSegment,
} from '@vexa/mixed-pipeline';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';
import type { Invocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';
import type { Pipeline, TranscriptSink } from './ports.js';

/** stt.v1 round-trip — real adapter = TranscriptionClient.transcribe; L2/L3 = a mock. The
 *  lane bakes language/prompt at the call site, so the closure carries the configured language. */
export type Transcribe = (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;

/** The Pipeline port extended with the capture entry the bridge pumps frames into. The
 *  orchestrator only sees start/stop; the capture bridge holds the BotPipeline to feedAudio. */
export interface BotPipeline extends Pipeline {
  /** One gmeet capture.v1 frame: CHANNEL index + glow NAME (undefined ⇒ no single glow now). */
  feedAudio(channel: number, glowName: string | undefined, pcm: Float32Array, tsMs: number): void;
  /** One mixed (Zoom/Teams) capture.v1 frame: a single mixed PCM stream, named downstream. */
  feedMixedAudio(pcm: Float32Array, tsMs: number): void;
  /** A mixed-lane "who is lit" hint (Zoom/Teams active-speaker), windowed by the namer. */
  recordHint(name: string, tMs: number, isEnd?: boolean): void;
}

/** The lane segments are the SEALED transcript.v1 view — structurally identical to the bot's
 *  contracts.ts TranscriptSegment (same SSOT schema). Map defensively so a future drift in
 *  either view is a compile error here, not a silent wire mismatch. */
function toBotSegment(seg: LaneSegment): TranscriptSegment {
  return {
    segment_id: seg.segment_id,
    speaker: seg.speaker,
    speaker_key: seg.speaker_key,
    text: seg.text,
    start: seg.start,
    end: seg.end,
    language: seg.language ?? undefined,
    completed: seg.completed,
    absolute_start_time: seg.absolute_start_time,
    absolute_end_time: seg.absolute_end_time,
    source: seg.source,
    confidence: seg.confidence,
    words: seg.words,
  };
}

/**
 * The sink ADAPTER — the load-bearing reconciliation. The lane emits via `segment` (confirmed),
 * `draft` (live partial, completed:false), `finalize` (session end); the bot's port is a single
 * `publish(segment)`. We forward BOTH `segment` and `draft` to publish (the bot's transcript.v1
 * egress carries `completed` to distinguish confirmed from draft) and treat `finalize` as a
 * no-op at this seam (the bot signals end-of-session via lifecycle.v1, not the transcript stream).
 * publish() is async; the lane's sink methods are sync fire-and-forget, so we swallow + log a
 * rejection rather than letting it escape the lane's emit path.
 */
function laneSink(publish: TranscriptSink['publish'], onError?: (e: unknown) => void): LaneTranscriptSink {
  const forward = (seg: LaneSegment): void => {
    void publish(toBotSegment(seg)).catch((e) => {
      (onError ?? ((err) => console.error(`[bot] pipeline: transcript publish rejected: ${String(err)}`)))(e);
    });
  };
  return {
    segment: forward,
    draft: forward,
    finalize() { /* session end is a lifecycle.v1 concern, not a transcript.v1 segment */ },
  };
}

/** A ChunkSegment (mixed lane) → the bot's transcript.v1 segment. The mixed lane publishes
 *  per-speaker bundles (confirmed + pending); we map each to a segment carrying `speaker` and
 *  `completed`. Audio-time is ms there → seconds here (transcript.v1 timing is seconds). */
function chunkToBotSegment(speaker: string, c: ChunkSegment, completed: boolean): TranscriptSegment {
  return {
    segment_id: c.segmentId,
    speaker,
    speaker_key: c.segmentId,
    text: c.text,
    start: c.startMs / 1000,
    end: c.endMs / 1000,
    language: c.language,
    completed,
    source: 'merged',
  };
}

/** Build the gmeet (per-channel) BotPipeline. The lane is lazy — it begins on the first fed
 *  frame (post-admission), so start() is a no-op and stop() disposes (flush every turn → finalize). */
function createGmeetBotPipeline(
  transcribe: Transcribe,
  sink: TranscriptSink,
  config?: SpeakerStreamManagerConfig,
  onError?: (e: unknown) => void,
): BotPipeline {
  const lane = createGmeetPipeline({ transcribe, sink: laneSink(sink.publish, onError), config, onError });
  return {
    async start() { /* lane is lazy — begins on the first fed frame */ },
    async stop() { await lane.dispose(); },
    feedAudio: (channel, glowName, pcm, tsMs) => lane.feedAudio(channel, glowName, pcm, tsMs),
    feedMixedAudio() { /* not the gmeet lane */ },
    recordHint() { /* not the gmeet lane */ },
  };
}

/** Build the mixed (Zoom/Teams) BotPipeline over @vexa/mixed-pipeline's ChunkedTranscriber.
 *  ChunkedTranscriber.create is async (it constructs the pyannote segmenter), so the transcriber
 *  is built on the first start()/feed; we lazily await it and queue nothing before it's ready
 *  (frames before create resolves are dropped — the model needs seconds to lock on regardless). */
function createMixedBotPipeline(
  transcribe: Transcribe,
  sink: TranscriptSink,
  language?: string,
  onError?: (e: unknown) => void,
): BotPipeline {
  let transcriber: ChunkedTranscriber | null = null;
  let creating: Promise<ChunkedTranscriber> | null = null;

  const publish = (speaker: string, segs: ChunkSegment[], completed: boolean): void => {
    for (const c of segs) {
      void sink.publish(chunkToBotSegment(speaker, c, completed)).catch((e) => {
        (onError ?? ((err) => console.error(`[bot] pipeline(mixed): publish rejected: ${String(err)}`)))(e);
      });
    }
  };

  const ensure = (): Promise<ChunkedTranscriber> => {
    if (transcriber) return Promise.resolve(transcriber);
    if (!creating) {
      creating = ChunkedTranscriber.create({
        transcribe,
        // ONE atomic bundle: newly-confirmed (persisted) + the surviving pending tail.
        publish: (speaker, confirmed, pending) => { publish(speaker, confirmed, true); publish(speaker, pending, false); },
        publishPending: (speaker, segments) => publish(speaker, segments, false),
        clearPending: () => { /* the bot's transcript.v1 egress is append-only; drafts self-replace by id */ },
        rename: (_oldSpeaker, newSpeaker, segments) => publish(newSpeaker, segments, true),
        language,
        onError,
      }).then((t) => { transcriber = t; return t; });
    }
    return creating;
  };

  return {
    async start() { await ensure(); },
    async stop() { if (transcriber) await transcriber.dispose(); },
    feedAudio() { /* not the mixed lane (mixed has no per-channel glow) */ },
    feedMixedAudio: (pcm, tsMs) => { transcriber?.feedAudio(pcm, tsMs); },
    recordHint: (name, tMs, isEnd) => { transcriber?.recordHint(name, 'dom-active', tMs, isEnd); },
  };
}

/** Build the real STT transcribe closure from invocation.v1 — language baked into the call so
 *  the lane never knows about config. transcribeEnabled=false ⇒ a no-op transcribe (the engine
 *  still runs turn gating but emits empty text; recording-only meetings need no STT). */
export function createTranscribe(inv: Invocation): Transcribe {
  if (inv.transcribeEnabled === false || !inv.transcriptionServiceUrl) {
    return async () => ({ text: '', language: inv.language ?? 'en', duration: 0, segments: [] });
  }
  const client = new TranscriptionClient({
    serviceUrl: inv.transcriptionServiceUrl,
    apiToken: inv.transcriptionServiceToken,
  });
  const language = inv.language ?? undefined;
  return (pcm, prompt) => client.transcribe(pcm, language, prompt);
}

/**
 * The composition-root factory: pick the lane on platform and wire stt + sink. Google Meet
 * uses the per-channel (overlap-safe, glow-named) lane; Zoom/Teams use the mixed lane.
 */
export function createBotPipeline(
  inv: Invocation,
  sink: TranscriptSink,
  opts: { transcribe?: Transcribe; config?: SpeakerStreamManagerConfig; onError?: (e: unknown) => void } = {},
): BotPipeline {
  const transcribe = opts.transcribe ?? createTranscribe(inv);
  if (inv.platform === 'zoom' || inv.platform === 'teams') {
    return createMixedBotPipeline(transcribe, sink, inv.language ?? undefined, opts.onError);
  }
  return createGmeetBotPipeline(transcribe, sink, opts.config, opts.onError);
}
