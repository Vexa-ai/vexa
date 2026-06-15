/**
 * gmeet-pipeline — the Google Meet PER-SPEAKER strategy of the pipeline brick.
 *
 *   capture.v1 (audio frames, speakerName bound at the SOURCE by the glow)
 *        ──►  transcript.v1 (named segments + live drafts)
 *
 * The inversion the gmeet rethink needs. Meet's remote channels are an anonymous
 * rotating pool (channel ≠ speaker), so this strategy IGNORES the channel index
 * entirely and routes audio by the bound glow NAME: one independent turn-gated
 * LocalAgreement stream (SpeakerStreamManager) PER NAME. No diarizer, no opaque
 * cluster keys, no post-hoc window-match — the name rode in on the audio.
 *
 * Why this also fixes "pending shows then lost": each name owns its stream, so its
 * segment ids are stable (`<name>:<seq>`) and a pending draft upgrades to confirmed
 * IN PLACE under the same key — unlike the mixed path, whose whole-turn
 * LocalAgreement accumulation churned pending/confirm ids on a single mixed stream.
 *
 * CONTRACT BOUNDARY: identity is CARRIED, never DERIVED here. capture bound the
 * name (glow); this brick only preserves it through transcription. Chunks with no
 * bound name (silence / overlap ⇒ undefined) go to the UNKNOWN stream so their
 * audio is still transcribed and shown — never dropped, never guessed.
 */
import { SpeakerStreamManager, type SpeakerStreamManagerConfig } from './speaker-streams';
import type { TranscriptionResult } from './transcription-client';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1';

export interface GmeetPipelineOptions {
  /** One Whisper round-trip (stt.v1). language is baked into the closure by the host. */
  transcribe: (pcm: Float32Array, prompt?: string) => Promise<TranscriptionResult>;
  /** Where transcript.v1 segments + drafts land (consumer = collector/rendering). */
  sink: TranscriptSink;
  /** Label for audio with no bound glow name (0 or 2+ lit). Default 'Speaker'. */
  unknownLabel?: string;
  /** SpeakerStreamManager tuning (turn gating / confirmation). */
  config?: SpeakerStreamManagerConfig;
}

export interface GmeetPipeline {
  /** One capture.v1 audio frame. speakerName is the glow name bound at the source
   *  (undefined ⇒ route to UNKNOWN). The channel index is deliberately NOT a param. */
  feedAudio(speakerName: string | undefined, pcm: Float32Array, tsMs: number): void;
  /** Force a final transcription of every open stream (no audio lost on close). */
  flush(): Promise<void>;
  /** Flush, drain in-flight transcriptions, then finalize the sink. */
  dispose(): Promise<void>;
}

/** Route a bound glow name to a stream key. Empty/undefined ⇒ the UNKNOWN stream.
 *  Pure + exported so the routing is golden-testable without audio. */
export function streamKeyFor(speakerName: string | undefined, unknownLabel: string): string {
  const n = speakerName && speakerName.trim();
  return n ? n : unknownLabel;
}

export function createGmeetPipeline(opts: GmeetPipelineOptions): GmeetPipeline {
  const UNKNOWN = opts.unknownLabel ?? 'Speaker';
  const mgr = new SpeakerStreamManager(opts.config);
  const inflight = new Set<Promise<void>>();

  // speakerKey IS the stream key (the bound name, or UNKNOWN) — the rendering keys
  // pending by speaker, so a draft and its confirm share that key and upgrade in place.
  const segOf = (speakerName: string, key: string, text: string, startMs: number, endMs: number): TranscriptSegment => ({
    speaker: speakerName, speakerKey: key, text,
    start: startMs / 1000, end: endMs / 1000, words: [],
    source: key === UNKNOWN ? 'provisional-cluster-id' : 'glow-bound',
    confidence: key === UNKNOWN ? 0 : 1, topology: 'per-participant',
  });

  mgr.onSegmentReady = (speakerId, _name, audio) => {
    const p = (async () => {
      try {
        const r = await opts.transcribe(audio, mgr.getLastConfirmedText(speakerId) || undefined);
        const text = (r?.text || '').trim();
        const segs = r?.segments;
        mgr.handleTranscriptionResult(speakerId, text, segs?.[segs.length - 1]?.end, segs);
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

  // Live forming tail → transcript.v1 draft channel. Empty text clears the draft.
  mgr.onSegmentPending = (speakerId, speakerName, text, startMs) => {
    opts.sink.draft?.({ ...segOf(speakerName, speakerId, text, startMs, startMs), confidence: 0 });
  };

  const settle = async () => { while (inflight.size) await Promise.all([...inflight]); };
  const flushAll = async () => { for (const id of mgr.getActiveSpeakers()) await mgr.flushSpeaker(id, true); await settle(); };

  return {
    feedAudio: (speakerName, pcm, tsMs) => {
      const key = streamKeyFor(speakerName, UNKNOWN);
      if (!mgr.hasSpeaker(key)) mgr.addSpeaker(key, key);
      mgr.feedAudio(key, pcm, tsMs);
    },
    flush: flushAll,
    dispose: async () => { await flushAll(); mgr.removeAll(); await opts.sink.finalize(); },
  };
}
