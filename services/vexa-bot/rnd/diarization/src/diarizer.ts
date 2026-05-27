/**
 * Diarizer interface — the single composition root for diarization backends.
 *
 * MVP0  : VadRoundRobinDiarizer (stub)
 * MVP1  : PyannoteSidecarDiarizer (Python child process holding pyannote 3.x)
 * MVP1+ : PseudoOracleScriptDiarizer (autonomous eval; reads pre-computed script)
 * MVP3  : DiartSidecarDiarizer / NeMoSortformerSidecarDiarizer (swap candidates)
 *
 * The harness picks one via DIARIZER env var. Downstream code (pipeline,
 * transcription, dashboard) is implementation-agnostic.
 */

export interface Diarizer {
  /** Called per audio frame. Returns the current speaker label. */
  process(audio: Float32Array, timestampMs: number): Promise<string>;
  /** Reset state — call on new meeting / harness restart. */
  reset(): void;
  /** Optional human-readable name for logs/dashboard. */
  readonly name: string;
}
