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
import { SpeakerStreamManager, type CoverageStats, type SpeakerStreamManagerConfig } from './speaker-streams.js';
import { log } from './log.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1.js';

export interface GmeetPipelineOptions {
  /** One Whisper round-trip (stt.v1). language is baked into the closure by the host. */
  transcribe: (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;
  /** Where transcript.v1 segments + drafts land (consumer = collector/rendering). */
  sink: TranscriptSink;
  /** Named attribution source. Null omits browser provenance for native identities. */
  speakerSource?: 'glow-bound' | null;
  /** Host-provided channel identity; defaults to the browser channel key. */
  channelKey?: (channel: number) => string;
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
  /** A closed turn is freed once it owes nothing (its last request answered); this bounds how long
   *  it may wait for a slow STT. Past it the turn is removed and its untranscribed audio is counted
   *  as dropped, loudly. Default 600000 (10 min — beyond the client's worst case of four timed-out
   *  attempts on a deferred final). */
  closeSettleCapMs?: number;
  /** Interval of the per-meeting `[COVERAGE]` log line (ms). 0 disables the periodic line; the
   *  line is always written once at dispose. Default 60000. */
  coverageLogIntervalMs?: number;
}

export interface GmeetPipeline {
  /** One capture.v1 frame: CHANNEL index + glow NAME (undefined ⇒ no single glow now). */
  feedAudio(channel: number, glowName: string | undefined, pcm: Float32Array, tsMs: number): void;
  flush(): Promise<void>;
  dispose(): Promise<void>;
  /** Seconds of audio captured / submitted / transcribed / skipped / dropped so far. */
  coverage(): CoverageStats;
}

/** One log line for a meeting's coverage: what was captured, what reached STT, where it ended up. */
export function formatCoverage(c: CoverageStats): string {
  const pct = c.capturedSec > 0 ? Math.round((c.transcribedSec / c.capturedSec) * 100) : 0;
  return `[COVERAGE] captured=${c.capturedSec}s submitted=${c.submittedSec}s transcribed=${c.transcribedSec}s ` +
    `(${pct}% of captured) no_speech=${c.noSpeechSec}s short=${c.shortSec}s dropped=${c.droppedSec}s`;
}

export function createGmeetPipeline(opts: GmeetPipelineOptions): GmeetPipeline {
  const UNKNOWN = opts.unknownLabel ?? 'Speaker';
  const ONSET_GAP = opts.onsetGapMs ?? 1000;
  const mgr = new SpeakerStreamManager(opts.config);
  const inflight = new Set<Promise<void>>();
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
      ...(!named ? {source: 'provisional-cluster-id' as const}
        : opts.speakerSource === null ? {} : {source: opts.speakerSource ?? 'glow-bound' as const}),
      confidence: named ? 1 : 0,
    };
  };

  // The window's language off the stt.v1 result: the per-call detection in auto mode, or the
  // invocation-forced code (baked into the transcribe closure, echoed back by the service).
  // 'unknown' is the client's no-detection sentinel, not an ISO code — NULL stays honest.
  const langOf = (l: string | undefined): string | undefined =>
    l && l !== 'unknown' ? l : undefined;

  mgr.onSegmentReady = (speakerId, _name, audio) => {
    const p = (async () => {
      try {
        const r = await opts.transcribe(audio, mgr.getLastConfirmedText(speakerId) || undefined);
        const segs = r?.segments;
        mgr.handleTranscriptionResult(speakerId, (r?.text || '').trim(), segs?.[segs.length - 1]?.end, segs, langOf(r?.language));
      } catch (e) {
        opts.onError?.(e);                          // P18: report the fault, don't swallow it…
        mgr.handleTranscriptionFault(speakerId, e);  // …a draft keeps its audio; a final counts its loss
      }
    })();
    inflight.add(p);
    void p.finally(() => inflight.delete(p));
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
  // The stream is freed only once it owes nothing: with STT answering in 5–30 s, a fixed delay
  // removed turns whose deferred final was still in flight, and their audio never reached the
  // transcript. The cap bounds a request that never answers (its loss is counted, loudly).
  const SETTLE_CAP = opts.closeSettleCapMs ?? 600_000;
  const closeTurn = (key: string) => {
    void mgr.flushSpeaker(key, true).catch(() => { /* nothing owed */ });
    const closedAt = Date.now();
    const tick = () => {
      if (mgr.isSettled(key) || Date.now() - closedAt > SETTLE_CAP) { mgr.removeSpeaker(key); return; }
      const t = setTimeout(tick, 1000);
      (t as { unref?: () => void }).unref?.();   // don't keep the process alive for cleanup
    };
    const t = setTimeout(tick, 1000);
    (t as { unref?: () => void }).unref?.();
  };
  const coverageEvery = opts.coverageLogIntervalMs ?? 60_000;
  let lastCoverageLine = '';
  const logCoverage = (force = false) => {
    const line = formatCoverage(mgr.coverage());
    if (force || line !== lastCoverageLine) log(line);
    lastCoverageLine = line;
  };
  const coverageTimer = coverageEvery > 0 ? setInterval(() => logCoverage(), coverageEvery) : undefined;
  (coverageTimer as { unref?: () => void } | undefined)?.unref?.();

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
        const key = `${opts.channelKey?.(channel) ?? `ch-${channel}`}:${turn}`;
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
      mgr.feedAudio(st.key, pcm, tsMs);
    },
    flush: async () => { for (const st of chan.values()) await mgr.flushSpeaker(st.key, true); await settle(); },
    coverage: () => mgr.coverage(),
    dispose: async () => {
      for (const st of chan.values()) await mgr.flushSpeaker(st.key, true);
      await settle();
      mgr.removeAll();
      if (coverageTimer) clearInterval(coverageTimer);
      logCoverage(true);
      await opts.sink.finalize();
    },
  };
}
