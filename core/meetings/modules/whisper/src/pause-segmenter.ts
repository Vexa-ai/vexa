/** ALLOY: Pure pause-boundary detection for opt-in per-chunk language re-detection. */
export interface AudioSampleRange {
  startSample: number;
  endSample: number;
}

const ANALYSIS_FRAME_MS = 20;
const MIN_PAUSE_MS = 350;
const MIN_ADJACENT_AUDIO_MS = 600;
const ABSOLUTE_SILENCE_RMS = 0.001;
const RELATIVE_SILENCE_RATIO = 0.08;

/**
 * ALLOY: Split a PCM window only at qualifying low-energy pause midpoints.
 * Returned ranges are contiguous and cover the input exactly once.
 */
export function splitOnPauses(
  pcm: Float32Array,
  sampleRate: number,
): AudioSampleRange[] {
  const fullRange = [{ startSample: 0, endSample: pcm.length }];
  if (pcm.length === 0 || !Number.isFinite(sampleRate) || sampleRate <= 0) return fullRange;

  const frameSize = Math.max(1, Math.round(sampleRate * ANALYSIS_FRAME_MS / 1000));
  const frameRms: number[] = [];

  for (let start = 0; start < pcm.length; start += frameSize) {
    const end = Math.min(pcm.length, start + frameSize);
    let sumSquares = 0;
    for (let i = start; i < end; i++) sumSquares += pcm[i] * pcm[i];
    const rms = Math.sqrt(sumSquares / (end - start));
    frameRms.push(rms);
  }

  // ALLOY: A high percentile represents ordinary speech energy without letting one click or
  // clipping spike raise the silence threshold for the rest of the window.
  const sortedRms = [...frameRms].sort((left, right) => left - right);
  const maxRms = sortedRms.at(-1) ?? 0;
  if (maxRms <= ABSOLUTE_SILENCE_RMS) return fullRange;
  const typicalSpeechRms = sortedRms[Math.floor((sortedRms.length - 1) * 0.9)] ?? maxRms;

  const silenceThreshold = Math.max(
    ABSOLUTE_SILENCE_RMS,
    typicalSpeechRms * RELATIVE_SILENCE_RATIO,
  );
  const minPauseSamples = Math.round(sampleRate * MIN_PAUSE_MS / 1000);
  const minAdjacentSamples = Math.round(sampleRate * MIN_ADJACENT_AUDIO_MS / 1000);
  const splitSamples: number[] = [];
  let silenceStart: number | undefined;
  let hasSpeechSinceLastSplit = false;

  for (let frame = 0; frame < frameRms.length; frame++) {
    const frameStart = frame * frameSize;
    if (frameRms[frame] <= silenceThreshold) {
      silenceStart ??= frameStart;
      continue;
    }
    if (silenceStart !== undefined) {
      const silenceEnd = frameStart;
      if (hasSpeechSinceLastSplit && silenceEnd - silenceStart >= minPauseSamples) {
        const midpoint = Math.round((silenceStart + silenceEnd) / 2);
        const previous = splitSamples.at(-1) ?? 0;
        if (
          midpoint - previous >= minAdjacentSamples
          && pcm.length - midpoint >= minAdjacentSamples
        ) {
          splitSamples.push(midpoint);
          hasSpeechSinceLastSplit = false;
        }
      }
      silenceStart = undefined;
    }
    hasSpeechSinceLastSplit = true;
  }

  if (splitSamples.length === 0) return fullRange;

  const ranges: AudioSampleRange[] = [];
  let startSample = 0;
  for (const endSample of splitSamples) {
    ranges.push({ startSample, endSample });
    startSample = endSample;
  }
  ranges.push({ startSample, endSample: pcm.length });
  return ranges;
}
