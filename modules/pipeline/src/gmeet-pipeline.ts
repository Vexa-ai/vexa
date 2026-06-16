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
import { SpeakerStreamManager, type SpeakerStreamManagerConfig } from './speaker-streams';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1';

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
  // Per channel: the CURRENT turn's stream key, bound name, last-audio time, turn counter.
  const chan = new Map<number, { key: string; name: string; lastMs: number; turn: number }>();

  const segOf = (speakerName: string, key: string, text: string, startMs: number, endMs: number): TranscriptSegment => {
    const named = speakerName !== UNKNOWN;
    return {
      speaker: speakerName, speakerKey: key, text,
      start: startMs / 1000, end: endMs / 1000, words: [],
      source: named ? 'glow-bound' : 'provisional-cluster-id',
      confidence: named ? 1 : 0, topology: 'per-participant',
    };
  };

  mgr.onSegmentReady = (speakerId, _name, audio) => {
    const p = (async () => {
      try {
        const r = await opts.transcribe(audio, mgr.getLastConfirmedText(speakerId) || undefined);
        const segs = r?.segments;
        mgr.handleTranscriptionResult(speakerId, (r?.text || '').trim(), segs?.[segs.length - 1]?.end, segs);
      } catch {
        mgr.handleTranscriptionResult(speakerId, '');
      }
    })();
    inflight.add(p);
    void p.finally(() => inflight.delete(p));
  };

  mgr.onSegmentConfirmed = (speakerId, speakerName, text, startMs, endMs) => {
    if (!text.trim()) return;
    opts.sink.segment(segOf(speakerName, speakerId, text, startMs, endMs));
  };
  mgr.onSegmentPending = (speakerId, speakerName, text, startMs) => {
    opts.sink.draft?.({ ...segOf(speakerName, speakerId, text, startMs, startMs), confidence: 0 });
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
