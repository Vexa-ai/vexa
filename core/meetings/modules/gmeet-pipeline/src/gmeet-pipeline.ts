/**
 * gmeet-pipeline — Google Meet CHANNEL-routed strategy (overlap-safe).
 *
 *   capture.v1 (audio frames: CHANNEL index + glow NAME bound at the source)
 *        ──►  transcript.v1 (named segments + live drafts)
 *
 * The combination of main's overlap engine + glow naming. Google Meet delivers
 * each active speaker on a SEPARATE channel, so audio is routed by CHANNEL: two
 * speakers talking at once land on separate per-channel streams and are
 * transcribed INDEPENDENTLY — no muddling, onsets intact. The glow names each
 * channel-TURN, bound at the turn's ONSET (a single tile lit, before any overlap)
 * and HELD through the overlap (where per-frame glow would be ambiguous → UNKNOWN).
 *
 * Each (channel, turn) is its OWN SpeakerStreamManager stream with a name FIXED at
 * onset (`ch-<n>:<turn>`). A turn ends on a silence gap OR a confident glow-name
 * CHANGE (overlap rotates a channel mid-stream with no gap), opening a fresh turn —
 * so a stream's name never relabels mid-flight — no async flush/relabel race.
 *
 * CONTRACT BOUNDARY: identity is CARRIED (the glow bound it at capture), never
 * derived. No diarizer, no post-hoc window-match.
 */
import { SpeakerStreamManager, type SpeakerStreamManagerConfig } from './speaker-streams.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1.js';
import type { AlloySttTelemetryTracker } from './alloy-stt-telemetry.js';

export interface GmeetPipelineOptions {
  /** One Whisper round-trip (stt.v1). language is baked into the closure by the host. */
  transcribe: (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;
  /** Where transcript.v1 segments + drafts land (consumer = collector/rendering). */
  sink: TranscriptSink;
  /** Label for a turn whose onset had no single confident glow. Default 'Speaker'. */
  unknownLabel?: string;
  /** SpeakerStreamManager tuning (turn gating / confirmation). */
  config?: SpeakerStreamManagerConfig;
  /** Silence gap (ms) on a channel that ends its turn (→ re-bind on the next onset). Default 1000. */
  onsetGapMs?: number;
  /** Surface a transcribe FAILURE (P18: fail loud + attributable). The pipeline still
   *  degrades gracefully (empty turn) so it doesn't wedge, but it reports the fault here
   *  so the host can make it observable (a /ws health frame, telemetry, lifecycle) instead
   *  of a silent "no transcript". Receives the thrown value (e.g. a TranscriptionError). */
  onError?: (fault: unknown) => void;
  /** ALLOY: keep one active STT request plus one replaceable pending request per
   * physical Meet audio channel. Disabled by default to preserve Vexa behavior. */
  serializeTranscriptionByChannel?: boolean;
  /** ALLOY: optional state tracker for local STT queue telemetry. */
  alloySttTelemetry?: AlloySttTelemetryTracker;
}

export interface GmeetPipeline {
  /** One capture.v1 frame: CHANNEL index + glow NAME (undefined ⇒ no single glow now). */
  feedAudio(channel: number, glowName: string | undefined, pcm: Float32Array, tsMs: number): void;
  flush(): Promise<void>;
  dispose(): Promise<void>;
}

export function createGmeetPipeline(opts: GmeetPipelineOptions): GmeetPipeline {
  const UNKNOWN = opts.unknownLabel ?? 'Speaker';
  const ONSET_GAP = opts.onsetGapMs ?? 1000;
  const mgr = new SpeakerStreamManager(opts.config);
  const inflight = new Set<Promise<void>>();
  type TranscriptionRequest = {
    speakerId: string;
    audio: Float32Array;
    turn: number;
    audioEndMs: number;
  };
  type ChannelQueue = {
    running: boolean;
    activeTurn: number;
    /** ALLOY: deferred final resubmit owned by the active turn. */
    activeContinuation?: TranscriptionRequest;
    pending?: TranscriptionRequest;
  };
  const channelQueues = new Map<string, ChannelQueue>();
  const latestAudioEndBySpeakerId = new Map<string, number>();
  // Per channel: the CURRENT turn's stream key, bound name, last-audio time, turn counter.
  const chan = new Map<number, { key: string; name: string; lastMs: number; turn: number }>();

  // Emit the SEALED transcript.v1 shape (snake_case, segment_id + completed, source
  // in the contract's enum) — the pipeline IS the transcript.v1 producer, so its
  // output conforms to meetings/contracts/transcript.v1 (pinned by the replay golden).
  const segOf = (speakerName: string, key: string, text: string, startMs: number, endMs: number, completed: boolean, lang?: string): TranscriptSegment => {
    const named = speakerName !== UNKNOWN;
    return {
      segment_id: `${key}:${Math.round(startMs)}`,
      speaker: speakerName, speaker_key: key, text,
      start: startMs / 1000, end: endMs / 1000, completed, words: [],
      language: lang ?? null,
      source: named ? 'glow-bound' : 'provisional-cluster-id',
      confidence: named ? 1 : 0,
    };
  };

  // The window's language off the stt.v1 result: the per-call detection in auto mode, or the
  // invocation-forced code (baked into the transcribe closure, echoed back by the service).
  // 'unknown' is the client's no-detection sentinel, not an ISO code — NULL stays honest.
  const langOf = (l: string | undefined): string | undefined =>
    l && l !== 'unknown' ? l : undefined;

  const channelId = (speakerId: string): string => speakerId.split(':', 1)[0] ?? speakerId;
  const turnId = (speakerId: string): number => {
    const parsed = Number.parseInt(speakerId.split(':', 2)[1] ?? '', 10);
    return Number.isInteger(parsed) ? parsed : 0;
  };

  const transcribeOne = async ({
    speakerId,
    audio,
    audioEndMs,
  }: TranscriptionRequest): Promise<void> => {
    const physicalChannelId = channelId(speakerId);
    const audioSec = audio.length / 16_000;
    const startedAtMs = Date.now();
    opts.alloySttTelemetry?.started(physicalChannelId, audioSec);
    try {
      const r = await opts.transcribe(audio, mgr.getLastConfirmedText(speakerId) || undefined);
      opts.alloySttTelemetry?.completed({
        channelId: physicalChannelId,
        audioSec,
        audioEndMs,
        processingDurationMs: Date.now() - startedAtMs,
      });
      opts.alloySttTelemetry?.recovered();
      const segs = r?.segments;
      mgr.handleTranscriptionResult(speakerId, (r?.text || '').trim(), segs?.[segs.length - 1]?.end, segs, langOf(r?.language));
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      opts.alloySttTelemetry?.failed(physicalChannelId, {
        code: 'stt_failed',
        message: message.slice(0, 512),
      });
      opts.onError?.(e);                          // P18: report the fault, don't swallow it…
      mgr.handleTranscriptionResult(speakerId, '');   // …but still free the turn (graceful degrade)
    }
  };

  const track = (p: Promise<void>): void => {
    inflight.add(p);
    void p.finally(() => inflight.delete(p));
  };

  const dropQueuedRequest = (request: TranscriptionRequest): void => {
    mgr.removeSpeaker(request.speakerId);
  };

  const enqueueByChannel = (request: TranscriptionRequest): void => {
    const key = channelId(request.speakerId);
    const queue = channelQueues.get(key);
    if (queue?.running) {
      // ALLOY: a closing active turn can synchronously request one final STT pass
      // when its prior response lands. Preserve it ahead of the replaceable
      // pending turn so rotation never releases audio still owned by the active turn.
      if (request.turn === queue.activeTurn) {
        queue.activeContinuation = request;
        return;
      }
      if (request.turn < queue.activeTurn) {
        dropQueuedRequest(request);
        return;
      }
      if (queue.pending) {
        if (request.turn <= queue.pending.turn) {
          dropQueuedRequest(request);
          return;
        }
        // ALLOY: this turn was superseded before reaching STT. Release its manager
        // buffer so only the newest accumulated channel audio remains pending.
        opts.alloySttTelemetry?.superseded(
          key,
          queue.pending.audio.length / 16_000
        );
        dropQueuedRequest(queue.pending);
      }
      queue.pending = request;
      opts.alloySttTelemetry?.queued(key, request.audio.length / 16_000);
      return;
    }

    const nextQueue: ChannelQueue = { running: true, activeTurn: request.turn };
    channelQueues.set(key, nextQueue);
    track((async () => {
      let current: TranscriptionRequest | undefined = request;
      try {
        while (current) {
          nextQueue.activeTurn = current.turn;
          await transcribeOne(current);
          if (nextQueue.activeContinuation) {
            current = nextQueue.activeContinuation;
            nextQueue.activeContinuation = undefined;
          } else {
            current = nextQueue.pending;
            nextQueue.pending = undefined;
          }
        }
      } finally {
        nextQueue.running = false;
        channelQueues.delete(key);
      }
    })());
  };

  mgr.onSegmentReady = (speakerId, _name, audio) => {
    const request = {
      speakerId,
      audio,
      turn: turnId(speakerId),
      audioEndMs: latestAudioEndBySpeakerId.get(speakerId) ?? 0,
    };
    if (opts.serializeTranscriptionByChannel) {
      enqueueByChannel(request);
      return;
    }
    track(transcribeOne(request));
  };

  mgr.onSegmentConfirmed = (speakerId, speakerName, text, startMs, endMs, _segmentId, lang) => {
    if (!text.trim()) return;
    opts.sink.segment(segOf(speakerName, speakerId, text, startMs, endMs, true, lang));
  };
  mgr.onSegmentPending = (speakerId, speakerName, text, startMs, lang) => {
    opts.sink.draft?.({ ...segOf(speakerName, speakerId, text, startMs, startMs, false, lang), confidence: 0 });
  };

  const settle = async () => { while (inflight.size) await Promise.all([...inflight]); };
  // Close a finished turn: final-submit + emit (name is fixed on the key, so the late
  // transcribe can't be mislabeled), then free the stream after it has long settled.
  const closeTurn = (key: string) => {
    void mgr.flushSpeaker(key, true).catch(() => { /* nothing owed */ });
    const t = setTimeout(() => mgr.removeSpeaker(key), 12000);
    (t as { unref?: () => void }).unref?.();   // don't keep the process alive for cleanup
  };

  return {
    feedAudio: (channel, glowName, pcm, tsMs) => {
      let st = chan.get(channel);
      // A channel-turn ends on EITHER a silence gap OR a confident glow-name CHANGE.
      // The glow-change case is the one overlap breaks: a channel rotates to a new
      // speaker mid-stream with NO silence gap, so the gap alone would hold the stale
      // name (the Галина→Зоя mislabel). A different single glow IS the rotation signal.
      const gapOnset = !!st && tsMs - st.lastMs > ONSET_GAP;
      const glowRotation = !!st && !!glowName && st.name !== UNKNOWN && glowName !== st.name;
      if (!st || gapOnset || glowRotation) {
        // TURN ONSET / rotation: close the previous turn and open a fresh stream named
        // from the glow lit RIGHT NOW (fixed for the turn — held through overlap below).
        if (st) closeTurn(st.key);
        const turn = (st ? st.turn : 0) + 1;
        const key = `ch-${channel}:${turn}`;
        st = { key, name: glowName || UNKNOWN, lastMs: tsMs, turn };
        chan.set(channel, st);
        mgr.addSpeaker(key, st.name);
      } else if (st.name === UNKNOWN && glowName) {
        // Onset was during overlap (no single glow) → opened UNKNOWN; a confident single
        // glow has now appeared early in the turn → adopt it (upgrade unknown→name only).
        st.name = glowName;
        mgr.updateSpeakerName(st.key, glowName);
      }
      st.lastMs = tsMs;
      const audioEndMs = tsMs + pcm.length / 16;
      latestAudioEndBySpeakerId.set(st.key, audioEndMs);
      opts.alloySttTelemetry?.captured(`ch-${channel}`, audioEndMs);
      mgr.feedAudio(st.key, pcm, tsMs);
    },
    flush: async () => { for (const st of chan.values()) await mgr.flushSpeaker(st.key, true); await settle(); },
    dispose: async () => {
      for (const st of chan.values()) await mgr.flushSpeaker(st.key, true);
      await settle();
      mgr.removeAll();
      await opts.sink.finalize();
    },
  };
}
