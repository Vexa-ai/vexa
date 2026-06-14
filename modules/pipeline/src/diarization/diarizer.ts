/**
 * Diarizer — the ONE seam this pack adds.
 *
 * It replaces the speaker-attribution step that happens before
 * `speakerManager.feedAudio(...)`. Everything downstream
 * (SpeakerStreamManager + TranscriptionClient + SegmentPublisher) is the
 * production bot's code, imported and used unmodified.
 *
 * Contract:
 *   Per audio frame, return a stable (speakerId, speakerName) pair the
 *   harness can feed to the bot's SpeakerStreamManager. The bot's per-
 *   speaker sliding-window buffering and Whisper word-prefix
 *   confirmation kick in automatically once the label is known.
 *
 * Implementations:
 *   MVP0 : VadRoundRobinDiarizer (bot's Silero VAD; rotates speaker_N on
 *          each silence→speech transition). Plumbing proof — not real
 *          voice discrimination.
 *   MVP1 : PyannoteSidecarDiarizer (Python child process with pyannote
 *          3.x). Real diarization. Same interface; one-line swap.
 *   MVP3 : Diart / NeMo Sortformer alternatives for backend comparison.
 */

export interface DiarizerLabel {
  /** Stable identity used as the SpeakerStreamManager speakerId.
   *  MVP0: "speaker_0".."speaker_{N-1}"; MVP1+: a clustered identity from the diarization model. */
  speakerId: string;
  /** Human-readable name shown in the dashboard. MVP0: same as speakerId. */
  speakerName: string;
}

export interface Diarizer {
  /** Per-frame call. Audio is Float32 PCM, 16kHz mono. */
  process(audio: Float32Array, timestampMs: number): Promise<DiarizerLabel>;
  /** Reset state on new session or harness restart. */
  reset(): void;
  /** Disposable name for logs/dashboard. */
  readonly name: string;
  /** Optional: transitive cluster-id rewrites accumulated by post-hoc
   *  merges. Implementations that don't merge clusters can omit this; the
   *  harness treats `undefined` the same as an empty map. */
  getLabelRewrites?(): Map<string, string>;
}
