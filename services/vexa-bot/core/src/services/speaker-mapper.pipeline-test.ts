/**
 * Multi-speaker end-to-end attribution pipeline test.
 *
 * Simulates the Teams pipeline with 3 speakers:
 *   Mixed audio (3 TTS files concatenated with gaps)
 *   -> SpeakerStreamManager -> Whisper (real, word timestamps)
 *   -> caption events with variable delay
 *   -> mapWordsToSpeakers(whisperWords, captionBoundaries)
 *   -> per-word attribution accuracy vs ground truth
 *
 * Run: npx ts-node core/src/services/speaker-mapper.pipeline-test.ts <audio-dir>
 */

import * as fs from 'fs';
import { SpeakerStreamManager } from './speaker-streams';
import { TranscriptionClient } from './transcription-client';
import { mapWordsToSpeakers, captionsToSpeakerBoundaries, TimestampedWord, SpeakerBoundary } from './speaker-mapper';

const SAMPLE_RATE = 16000;
const CHUNK_SIZE = 4096;
const CHUNK_DURATION_MS = (CHUNK_SIZE / SAMPLE_RATE) * 1000;
const TX_URL = process.env.TRANSCRIPTION_URL || 'http://localhost:8085/v1/audio/transcriptions';
const TX_TOKEN = process.env.TRANSCRIPTION_TOKEN || '32c59b9f654f1b6e376c6f020d79897d';

const AUDIO_DIR = process.argv[2] || `${__dirname}/../../../../features/realtime-transcription/data/raw`;

// ── WAV reader (resamples to SAMPLE_RATE) ──────────────────────────

function readWavAsFloat32(path: string): Float32Array {
  const buf = fs.readFileSync(path);
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error(`Not a WAV: ${path}`);
  const sampleRate = buf.readUInt32LE(24);
  const bitsPerSample = buf.readUInt16LE(34);
  const numChannels = buf.readUInt16LE(22);
  let dataOffset = 36;
  while (dataOffset < buf.length - 8) {
    if (buf.toString('ascii', dataOffset, dataOffset + 4) === 'data') { dataOffset += 8; break; }
    dataOffset += 8 + buf.readUInt32LE(dataOffset + 4);
  }
  const bytesPerSample = bitsPerSample / 8;
  const totalSamples = (buf.length - dataOffset) / (bytesPerSample * numChannels);
  const original = new Float32Array(totalSamples);
  for (let i = 0; i < totalSamples; i++) {
    const pos = dataOffset + i * bytesPerSample * numChannels;
    original[i] = bitsPerSample === 16 ? buf.readInt16LE(pos) / 32768 : buf.readFloatLE(pos);
  }
  if (sampleRate === SAMPLE_RATE) return original;
  const ratio = SAMPLE_RATE / sampleRate;
  const resampled = new Float32Array(Math.floor(totalSamples * ratio));
  for (let i = 0; i < resampled.length; i++) {
    const srcIdx = i / ratio;
    const lo = Math.floor(srcIdx);
    const hi = Math.min(lo + 1, totalSamples - 1);
    resampled[i] = original[lo] * (1 - srcIdx + lo) + original[hi] * (srcIdx - lo);
  }
  return resampled;
}

// ── Caption delay simulation ───────────────────────────────────────

function simulateCaptionDelay(): number {
  // Normal distribution: mean=1.5s, std=0.5s, clamped to [0.5, 3.0]
  const u1 = Math.random(), u2 = Math.random();
  const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  return Math.max(0.5, Math.min(3.0, 1.5 + z * 0.5));
}

interface GroundTruthSegment {
  speaker: string;
  startSec: number;
  endSec: number;
  text: string;
}

function generateCaptionEvents(groundTruth: GroundTruthSegment[]): { speaker: string; timestamp: number }[] {
  const events: { speaker: string; timestamp: number }[] = [];
  for (const seg of groundTruth) {
    const dur = seg.endSec - seg.startSec;
    // 3 caption events per segment: start, middle, end
    const offsets = [0, dur * 0.4, dur * 0.8];
    for (const off of offsets) {
      events.push({
        speaker: seg.speaker,
        timestamp: seg.startSec + off + simulateCaptionDelay(),
      });
    }
  }
  return events.sort((a, b) => a.timestamp - b.timestamp);
}

// ── Ground truth word lookup ───────────────────────────────────────

function groundTruthSpeakerForWord(word: TimestampedWord, groundTruth: GroundTruthSegment[]): string | null {
  const mid = (word.start + word.end) / 2;
  for (const seg of groundTruth) {
    if (mid >= seg.startSec && mid <= seg.endSec) return seg.speaker;
  }
  // Nearest segment
  let best: string | null = null;
  let bestDist = Infinity;
  for (const seg of groundTruth) {
    const dist = Math.min(Math.abs(mid - seg.startSec), Math.abs(mid - seg.endSec));
    if (dist < bestDist) { bestDist = dist; best = seg.speaker; }
  }
  return best;
}

// ── Main ───────────────────────────────────────────────────────────

async function main() {
  console.log('\n  ================================================');
  console.log('  Multi-Speaker Attribution Pipeline Test (3 speakers)');
  console.log('  Mixed audio -> Whisper -> caption boundaries -> mapper');
  console.log('  ================================================\n');

  // ── 1. Load audio files ──────────────────────────────────────────

  const aliceAudio = readWavAsFloat32(`${AUDIO_DIR}/short-sentence.wav`);
  const bobAudio   = readWavAsFloat32(`${AUDIO_DIR}/medium-paragraph.wav`);
  const charlieAudio = readWavAsFloat32(`${AUDIO_DIR}/short-sentence.wav`);

  const aliceDur   = aliceAudio.length / SAMPLE_RATE;
  const bobDur     = bobAudio.length / SAMPLE_RATE;
  const charlieDur = charlieAudio.length / SAMPLE_RATE;
  const gapDur     = 2.0;

  // Concatenate: Alice -> 2s gap -> Bob -> 2s gap -> Charlie
  const gapSamples = Math.floor(gapDur * SAMPLE_RATE);
  const totalSamples = aliceAudio.length + gapSamples + bobAudio.length + gapSamples + charlieAudio.length;
  const mixed = new Float32Array(totalSamples);

  let offset = 0;
  mixed.set(aliceAudio, offset);
  offset += aliceAudio.length + gapSamples;
  mixed.set(bobAudio, offset);
  offset += bobAudio.length + gapSamples;
  mixed.set(charlieAudio, offset);

  const totalDur    = mixed.length / SAMPLE_RATE;
  const bobStart    = aliceDur + gapDur;
  const charlieStart = bobStart + bobDur + gapDur;

  console.log(`  Alice:   0.0s - ${aliceDur.toFixed(1)}s  (short-sentence.wav)`);
  console.log(`  Gap:     ${aliceDur.toFixed(1)}s - ${bobStart.toFixed(1)}s  (silence)`);
  console.log(`  Bob:     ${bobStart.toFixed(1)}s - ${(bobStart + bobDur).toFixed(1)}s  (medium-paragraph.wav)`);
  console.log(`  Gap:     ${(bobStart + bobDur).toFixed(1)}s - ${charlieStart.toFixed(1)}s  (silence)`);
  console.log(`  Charlie: ${charlieStart.toFixed(1)}s - ${totalDur.toFixed(1)}s  (short-sentence.wav)`);
  console.log(`  Total:   ${totalDur.toFixed(1)}s mixed audio\n`);

  // ── 2. Ground truth ──────────────────────────────────────────────

  const aliceGT   = fs.readFileSync(`${AUDIO_DIR}/short-sentence.txt`, 'utf8').trim();
  const bobGT     = fs.readFileSync(`${AUDIO_DIR}/medium-paragraph.txt`, 'utf8').trim();
  const charlieGT = aliceGT; // Same audio, different speaker

  const groundTruth: GroundTruthSegment[] = [
    { speaker: 'Alice',   startSec: 0,            endSec: aliceDur,          text: aliceGT },
    { speaker: 'Bob',     startSec: bobStart,      endSec: bobStart + bobDur, text: bobGT },
    { speaker: 'Charlie', startSec: charlieStart,  endSec: totalDur,          text: charlieGT },
  ];

  console.log('  Ground truth:');
  for (const seg of groundTruth) {
    console.log(`    ${seg.speaker}: ${seg.startSec.toFixed(1)}s - ${seg.endSec.toFixed(1)}s "${seg.text.substring(0, 60)}${seg.text.length > 60 ? '...' : ''}"`);
  }
  console.log('');

  // ── 3. Generate caption events with variable delay ───────────────

  const captionEvents = generateCaptionEvents(groundTruth);

  console.log('  Caption events (with variable delay, mean=1.5s):');
  for (const c of captionEvents) {
    console.log(`    ${c.timestamp.toFixed(2)}s: ${c.speaker}`);
  }
  console.log('');

  const speakerBoundaries = captionsToSpeakerBoundaries(captionEvents);
  console.log('  Speaker boundaries (from captions):');
  for (const b of speakerBoundaries) {
    console.log(`    ${b.speaker}: ${b.start.toFixed(2)}s - ${b.end.toFixed(2)}s`);
  }
  console.log('');

  // ── 4. Feed audio through SpeakerStreamManager -> Whisper ────────

  const txClient = new TranscriptionClient({
    serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15,
  });
  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE, minAudioDuration: 3, submitInterval: 3,
    confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15,
  });

  const t0 = Date.now();
  const ts = () => ((Date.now() - t0) / 1000).toFixed(1);
  const allWords: TimestampedWord[] = [];
  let whisperCalls = 0;

  mgr.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
    whisperCalls++;
    const callNum = whisperCalls;
    try {
      const result = await txClient.transcribe(audioBuffer);
      if (result?.text) {
        const text = result.text.trim();
        const words = result.segments?.flatMap(s => s.words || []) || [];
        console.log(`  [${ts()}s] WHISPER #${callNum} | ${words.length} words | "${text.substring(0, 80)}${text.length > 80 ? '...' : ''}"`);
        if (words.length > 0) {
          // Replace with latest full-buffer transcription (sliding window re-transcribes)
          allWords.length = 0;
          allWords.push(...words);
        }
        const lastSeg = result.segments?.[result.segments.length - 1];
        mgr.handleTranscriptionResult(speakerId, text, lastSeg?.end);
      } else {
        mgr.handleTranscriptionResult(speakerId, '');
      }
    } catch (err: any) {
      console.log(`  [${ts()}s] ERROR #${callNum} | ${err.message}`);
      mgr.handleTranscriptionResult(speakerId, '');
    }
  };

  mgr.onSegmentConfirmed = (speakerId, speakerName, text) => {
    console.log(`\n  CONFIRMED | "${text.substring(0, 80)}${text.length > 80 ? '...' : ''}"\n`);
  };

  // Feed mixed audio at real-time speed
  console.log(`  [0.0s] Playing ${totalDur.toFixed(1)}s of mixed audio at real-time speed...\n`);
  mgr.addSpeaker('mixed', 'Mixed');

  const totalChunks = Math.ceil(mixed.length / CHUNK_SIZE);
  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, mixed.length);
    mgr.feedAudio('mixed', mixed.subarray(start, end));

    const audioSec = (i * CHUNK_SIZE) / SAMPLE_RATE;
    if (i > 0 && Math.floor(audioSec) % 5 === 0 && Math.floor(((i - 1) * CHUNK_SIZE) / SAMPLE_RATE) % 5 !== 0) {
      console.log(`  [${ts()}s] --- ${audioSec.toFixed(0)}s / ${totalDur.toFixed(0)}s ---`);
    }

    await new Promise(r => setTimeout(r, CHUNK_DURATION_MS));
  }

  console.log(`\n  [${ts()}s] Audio playback complete. Waiting for final transcription...\n`);
  await new Promise(r => setTimeout(r, 3000));
  mgr.flushSpeaker('mixed', true);
  await new Promise(r => setTimeout(r, 3000));

  // ── 5. Run speaker attribution ───────────────────────────────────

  console.log('  ================================================');
  console.log('  SPEAKER ATTRIBUTION');
  console.log('  ================================================\n');

  if (allWords.length === 0) {
    console.log('  ERROR: No word timestamps from Whisper. Cannot attribute speakers.');
    console.log('  Check that transcription-service is running at', TX_URL);
    mgr.removeAll();
    process.exit(1);
  }

  console.log(`  ${allWords.length} words from Whisper, ${speakerBoundaries.length} speaker boundaries\n`);

  const attributed = mapWordsToSpeakers(allWords, speakerBoundaries);

  console.log('  Attributed segments:');
  for (const seg of attributed) {
    console.log(`    [${seg.speaker}] ${seg.start.toFixed(2)}s - ${seg.end.toFixed(2)}s (${seg.wordCount} words):`);
    console.log(`      "${seg.text}"\n`);
  }

  // ── 6. Per-word accuracy vs ground truth ─────────────────────────

  console.log('  ================================================');
  console.log('  ATTRIBUTION ACCURACY (per-word)');
  console.log('  ================================================\n');

  // Flatten attributed segments back to per-word with speaker label
  const wordAttribution: { word: TimestampedWord; attributedSpeaker: string }[] = [];
  for (const seg of attributed) {
    for (const w of seg.words) {
      wordAttribution.push({ word: w, attributedSpeaker: seg.speaker });
    }
  }

  // Per-speaker stats
  const speakerStats: Record<string, { correct: number; total: number; misattributed: { word: string; expected: string; got: string; time: number }[] }> = {
    'Alice':   { correct: 0, total: 0, misattributed: [] },
    'Bob':     { correct: 0, total: 0, misattributed: [] },
    'Charlie': { correct: 0, total: 0, misattributed: [] },
  };

  let totalCorrect = 0;
  let totalWords = 0;

  for (const { word, attributedSpeaker } of wordAttribution) {
    const gtSpeaker = groundTruthSpeakerForWord(word, groundTruth);
    if (!gtSpeaker) continue;

    totalWords++;
    if (!speakerStats[gtSpeaker]) {
      speakerStats[gtSpeaker] = { correct: 0, total: 0, misattributed: [] };
    }
    speakerStats[gtSpeaker].total++;

    if (attributedSpeaker === gtSpeaker) {
      speakerStats[gtSpeaker].correct++;
      totalCorrect++;
    } else {
      speakerStats[gtSpeaker].misattributed.push({
        word: word.word.trim(),
        expected: gtSpeaker,
        got: attributedSpeaker,
        time: word.start,
      });
    }
  }

  // Print per-speaker results
  for (const speaker of ['Alice', 'Bob', 'Charlie']) {
    const stats = speakerStats[speaker];
    if (!stats || stats.total === 0) {
      console.log(`  ${speaker}: no words found in ground truth range`);
      continue;
    }
    const pct = ((stats.correct / stats.total) * 100).toFixed(1);
    console.log(`  ${speaker}: ${stats.correct}/${stats.total} correct (${pct}%), ${stats.misattributed.length} misattributed`);
    if (stats.misattributed.length > 0) {
      for (const m of stats.misattributed.slice(0, 5)) {
        console.log(`    "${m.word}" at ${m.time.toFixed(2)}s: expected ${m.expected}, got ${m.got}`);
      }
      if (stats.misattributed.length > 5) {
        console.log(`    ... and ${stats.misattributed.length - 5} more`);
      }
    }
  }

  const overallPct = totalWords > 0 ? ((totalCorrect / totalWords) * 100).toFixed(1) : '0.0';
  console.log(`\n  OVERALL: ${totalCorrect}/${totalWords} words correctly attributed (${overallPct}%)`);

  // ── 7. Summary ───────────────────────────────────────────────────

  console.log('\n  ================================================');
  console.log('  SUMMARY');
  console.log('  ================================================\n');
  console.log(`  Speakers:        3 (Alice, Bob, Charlie)`);
  console.log(`  Audio duration:  ${totalDur.toFixed(1)}s`);
  console.log(`  Whisper calls:   ${whisperCalls}`);
  console.log(`  Words returned:  ${allWords.length}`);
  console.log(`  Caption events:  ${captionEvents.length} (variable delay, mean=1.5s)`);
  console.log(`  Boundaries:      ${speakerBoundaries.length}`);
  console.log(`  Attribution:     ${overallPct}% correct`);
  console.log('');

  mgr.removeAll();
  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e); process.exit(1); });
