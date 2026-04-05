/**
 * Real-world data replay test for the Teams transcription pipeline.
 *
 * Replays a real Teams meeting (Session 1: 4-turn Alice/Bob conversation):
 *   - Reconstructs audio from TTS WAV files timed to ground truth send times
 *   - Replays actual caption events at their recorded timestamps
 *   - Full pipeline: SpeakerStreamManager -> Whisper -> speaker-mapper
 *   - Measures per-word attribution accuracy against ground truth
 *
 * Part A: Pure speaker-mapper test with real caption data (no Whisper, instant)
 * Part B: Full audio replay with real-time caption events (needs Whisper)
 *
 * Usage:
 *   npx ts-node core/src/services/replay-meeting.test.ts <audio-dir> <test-data-dir>
 *
 * Example:
 *   npx ts-node core/src/services/replay-meeting.test.ts \
 *     features/realtime-transcription/data/raw \
 *     features/realtime-transcription/tests
 */

import * as fs from 'fs';
import { SpeakerStreamManager } from './speaker-streams';
import { TranscriptionClient } from './transcription-client';
import {
  mapWordsToSpeakers,
  captionsToSpeakerBoundaries,
  TimestampedWord,
  CaptionEvent,
  SpeakerBoundary,
  AttributedSegment,
} from './speaker-mapper';

// ── Config ──────────────────────────────────────────────────────────

const SAMPLE_RATE = 16000;
const CHUNK_SIZE = 4096;
const CHUNK_DURATION_MS = (CHUNK_SIZE / SAMPLE_RATE) * 1000; // ~256ms
const TX_URL = process.env.TRANSCRIPTION_URL || 'http://localhost:8085/v1/audio/transcriptions';
const TX_TOKEN = process.env.TRANSCRIPTION_TOKEN || '32c59b9f654f1b6e376c6f020d79897d';

const AUDIO_DIR = process.argv[2] || `${__dirname}/../../../../features/realtime-transcription/data/raw`;
const TEST_DIR = process.argv[3] || `${__dirname}/../../../../features/realtime-transcription/tests`;

// ── Ground truth: Session 1 TTS texts and send times ────────────────
// Parsed from reference-ground-truth-normal.txt

interface GroundTruthTurn {
  speaker: string;
  sendTime: number;   // Unix epoch seconds (from [GT] lines)
  text: string;        // TTS input text
  audioFile: string;   // WAV file to use for this turn
}

const SESSION1_TURNS: GroundTruthTurn[] = [
  {
    speaker: 'Alice',
    sendTime: 1774018355.367,
    text: 'Good morning everyone. I want to start by reviewing our product metrics from last month. We had over fifty thousand active users which is a new record for us.',
    audioFile: 'medium-paragraph.wav',  // 13.6s — contains full Alice+Bob TTS content, trim to first ~10s
  },
  {
    speaker: 'Bob',
    sendTime: 1774018369.390,
    text: 'Those numbers are impressive Alice. Can you break down the user growth by region? I am particularly interested in the European market expansion.',
    audioFile: 'long-dialogue.wav',     // 17.7s — dialogue audio, closest match for Bob's turn
  },
  {
    speaker: 'Alice',
    sendTime: 1774018383.413,
    text: 'Sure. Europe grew by twenty percent, Asia by fifteen percent, and North America by ten percent.',
    audioFile: 'short-sentence.wav',    // 2.8s — short turn, pad with silence
  },
  {
    speaker: 'Bob',
    sendTime: 1774018393.438,
    text: 'Makes sense. Localization always helps.',
    audioFile: 'short-sentence.wav',    // 2.8s — short turn
  },
];

// Conversation ends at 1774018401.441 (from ground truth)
const SESSION1_END = 1774018401.441;
const SESSION1_START = SESSION1_TURNS[0].sendTime;

// ── WAV reader (resamples to SAMPLE_RATE) ───────────────────────────

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

// ── Reference data loader ───────────────────────────────────────────

interface ReferenceEvent {
  timestamp: string;
  type: string;
  speaker?: string;
  text?: string;
  from_speaker?: string;
  to_speaker?: string;
  start_sec?: number;
  end_sec?: number;
  language?: string;
  chunks?: number;
}

interface ReferenceData {
  stats: Record<string, number>;
  total: number;
  events: ReferenceEvent[];
}

function loadReferenceData(): ReferenceData {
  const path = `${TEST_DIR}/reference-timestamped-data.json`;
  return JSON.parse(fs.readFileSync(path, 'utf8'));
}

// Filter to Session 1 only: events before 14:54 (before Dmitry's solo session)
function session1Events(data: ReferenceData): ReferenceEvent[] {
  return data.events.filter(e => e.timestamp < '2026-03-20T14:54');
}

// Extract caption_text events -> CaptionEvent format for captionsToSpeakerBoundaries
function extractCaptionEvents(events: ReferenceEvent[]): CaptionEvent[] {
  const t0 = new Date(events[0].timestamp).getTime() / 1000;
  return events
    .filter(e => e.type === 'caption_text' && e.speaker)
    .map(e => ({
      speaker: e.speaker!,
      text: e.text || '',
      timestamp: (new Date(e.timestamp).getTime() / 1000) - t0,
    }));
}

// Extract confirmed segments from reference data
function extractConfirmedSegments(events: ReferenceEvent[]): {
  speaker: string; text: string; start_sec: number; end_sec: number;
}[] {
  return events
    .filter(e => e.type === 'confirmed' && e.speaker && e.text)
    .map(e => ({
      speaker: e.speaker!,
      text: e.text!,
      start_sec: e.start_sec!,
      end_sec: e.end_sec!,
    }));
}

// ── Ground truth speaker assignment ─────────────────────────────────

// Session 1 speaker time ranges (from caption speaker_change events):
//   Alice: 0s - 13.56s (first caption to Bob's first caption)
//   Bob: 13.56s - 28.16s
//   Alice: 28.16s - 37.46s
//   Bob: 37.46s - end
// These are relative to the first caption event timestamp.

interface GroundTruthRange {
  speaker: string;
  startSec: number;
  endSec: number;
  text: string;
}

function buildGroundTruthRanges(captionEvents: CaptionEvent[]): GroundTruthRange[] {
  // Find speaker transitions from the actual caption data
  const ranges: GroundTruthRange[] = [];
  let currentSpeaker = captionEvents[0].speaker;
  let segStart = captionEvents[0].timestamp;

  for (let i = 1; i < captionEvents.length; i++) {
    if (captionEvents[i].speaker !== currentSpeaker) {
      ranges.push({
        speaker: normalizeSpeakerName(currentSpeaker),
        startSec: segStart,
        endSec: captionEvents[i].timestamp,
        text: '',
      });
      currentSpeaker = captionEvents[i].speaker;
      segStart = captionEvents[i].timestamp;
    }
  }
  // Close last range
  ranges.push({
    speaker: normalizeSpeakerName(currentSpeaker),
    startSec: segStart,
    endSec: captionEvents[captionEvents.length - 1].timestamp + 10,
    text: '',
  });

  // Assign ground truth text from SESSION1_TURNS
  const turnTexts = SESSION1_TURNS.map(t => t.text);
  for (let i = 0; i < Math.min(ranges.length, turnTexts.length); i++) {
    ranges[i].text = turnTexts[i];
  }

  return ranges;
}

function normalizeSpeakerName(name: string): string {
  // "Alice (Guest)" -> "Alice", "Bob (Guest)" -> "Bob"
  return name.replace(/\s*\(Guest\)\s*$/, '');
}

function groundTruthSpeakerForWord(
  word: TimestampedWord,
  ranges: GroundTruthRange[],
): string | null {
  const mid = (word.start + word.end) / 2;
  for (const r of ranges) {
    if (mid >= r.startSec && mid <= r.endSec) return r.speaker;
  }
  // Nearest range
  let best: string | null = null;
  let bestDist = Infinity;
  for (const r of ranges) {
    const dist = Math.min(Math.abs(mid - r.startSec), Math.abs(mid - r.endSec));
    if (dist < bestDist) { bestDist = dist; best = r.speaker; }
  }
  return best;
}

// ── Scoring ─────────────────────────────────────────────────────────

interface SpeakerScore {
  correct: number;
  total: number;
  misattributed: { word: string; expected: string; got: string; time: number }[];
}

function scoreAttribution(
  attributed: AttributedSegment[],
  groundTruth: GroundTruthRange[],
): { overall: { correct: number; total: number; pct: string }; perSpeaker: Record<string, SpeakerScore> } {
  const perSpeaker: Record<string, SpeakerScore> = {};
  let totalCorrect = 0;
  let totalWords = 0;

  for (const seg of attributed) {
    for (const w of seg.words) {
      const gtSpeaker = groundTruthSpeakerForWord(w, groundTruth);
      if (!gtSpeaker) continue;

      totalWords++;
      if (!perSpeaker[gtSpeaker]) {
        perSpeaker[gtSpeaker] = { correct: 0, total: 0, misattributed: [] };
      }
      perSpeaker[gtSpeaker].total++;

      const attrNorm = normalizeSpeakerName(seg.speaker);
      if (attrNorm === gtSpeaker) {
        perSpeaker[gtSpeaker].correct++;
        totalCorrect++;
      } else {
        perSpeaker[gtSpeaker].misattributed.push({
          word: w.word.trim(),
          expected: gtSpeaker,
          got: attrNorm,
          time: w.start,
        });
      }
    }
  }

  const pct = totalWords > 0 ? ((totalCorrect / totalWords) * 100).toFixed(1) : '0.0';
  return { overall: { correct: totalCorrect, total: totalWords, pct }, perSpeaker };
}

function printScores(
  label: string,
  scores: ReturnType<typeof scoreAttribution>,
): void {
  console.log(`\n  ${label}`);
  console.log(`  ${'='.repeat(label.length)}\n`);

  for (const speaker of Object.keys(scores.perSpeaker).sort()) {
    const s = scores.perSpeaker[speaker];
    if (s.total === 0) continue;
    const pct = ((s.correct / s.total) * 100).toFixed(1);
    console.log(`  ${speaker}: ${s.correct}/${s.total} correct (${pct}%)`);
    if (s.misattributed.length > 0) {
      for (const m of s.misattributed.slice(0, 5)) {
        console.log(`    "${m.word}" at ${m.time.toFixed(2)}s: expected ${m.expected}, got ${m.got}`);
      }
      if (s.misattributed.length > 5) {
        console.log(`    ... and ${s.misattributed.length - 5} more`);
      }
    }
  }

  console.log(`\n  OVERALL: ${scores.overall.correct}/${scores.overall.total} words (${scores.overall.pct}%)`);
}

// ── Part A: Pure speaker-mapper test (no Whisper) ───────────────────

function runPartA(): void {
  console.log('\n  ========================================================');
  console.log('  PART A: Speaker-mapper with real caption boundaries');
  console.log('  (No Whisper -- synthetic word timestamps from confirmed text)');
  console.log('  ========================================================\n');

  const data = loadReferenceData();
  const s1 = session1Events(data);
  const captionEvents = extractCaptionEvents(s1);
  const confirmed = extractConfirmedSegments(s1);

  console.log(`  Loaded ${captionEvents.length} caption events, ${confirmed.length} confirmed segments`);

  // Build speaker boundaries from real caption data
  const boundaries = captionsToSpeakerBoundaries(captionEvents);

  console.log(`\n  Speaker boundaries (from real captions):`);
  for (const b of boundaries) {
    console.log(`    ${normalizeSpeakerName(b.speaker)}: ${b.start.toFixed(2)}s - ${b.end.toFixed(2)}s`);
  }

  // Build ground truth ranges from the caption transitions
  const gtRanges = buildGroundTruthRanges(captionEvents);
  console.log(`\n  Ground truth ranges:`);
  for (const r of gtRanges) {
    console.log(`    ${r.speaker}: ${r.startSec.toFixed(2)}s - ${r.endSec.toFixed(2)}s`);
  }

  // Create synthetic word timestamps from confirmed segments.
  // Distribute words evenly across the confirmed segment's time range.
  const syntheticWords: TimestampedWord[] = [];
  for (const seg of confirmed) {
    const words = seg.text.split(/\s+/).filter(w => w.length > 0);
    if (words.length === 0) continue;
    const dur = seg.end_sec - seg.start_sec;
    const wordDur = dur / words.length;
    for (let i = 0; i < words.length; i++) {
      syntheticWords.push({
        word: words[i],
        start: seg.start_sec + i * wordDur,
        end: seg.start_sec + (i + 1) * wordDur,
      });
    }
  }

  console.log(`\n  Synthetic word timestamps: ${syntheticWords.length} words`);
  console.log(`  Time range: ${syntheticWords[0]?.start.toFixed(2)}s - ${syntheticWords[syntheticWords.length - 1]?.end.toFixed(2)}s`);

  // The confirmed segments use internal Whisper offsets (start_sec relative to
  // speaker buffer start), but caption timestamps are relative to the first
  // caption event. We need to align them.
  //
  // From the data: first confirmed segment starts at 26.8s (Whisper offset),
  // first caption event is at relative 0s, but the actual first caption
  // arrives at the same wall-clock moment as audio offset ~26.8s.
  //
  // So: word timestamp (Whisper offset) = caption timestamp (relative) + offset
  // where offset = first confirmed start_sec - first caption timestamp
  // = 26.8 - 0 = 26.8

  const whisperOffset = confirmed.length > 0 ? confirmed[0].start_sec : 0;
  const captionOffset = captionEvents.length > 0 ? captionEvents[0].timestamp : 0;
  const alignmentShift = whisperOffset - captionOffset;

  console.log(`\n  Alignment: Whisper offset = ${whisperOffset.toFixed(1)}s, caption offset = ${captionOffset.toFixed(2)}s`);
  console.log(`  Shift caption boundaries by +${alignmentShift.toFixed(2)}s to match Whisper timestamps`);

  // Shift boundaries to Whisper's coordinate system
  const alignedBoundaries: SpeakerBoundary[] = boundaries.map(b => ({
    speaker: b.speaker,
    start: b.start + alignmentShift,
    end: b.end + alignmentShift,
  }));

  console.log(`\n  Aligned speaker boundaries:`);
  for (const b of alignedBoundaries) {
    console.log(`    ${normalizeSpeakerName(b.speaker)}: ${b.start.toFixed(2)}s - ${b.end.toFixed(2)}s`);
  }

  // Run speaker-mapper
  const attributed = mapWordsToSpeakers(syntheticWords, alignedBoundaries);

  console.log(`\n  Attributed segments:`);
  for (const seg of attributed) {
    const textPreview = seg.text.length > 70 ? seg.text.substring(0, 70) + '...' : seg.text;
    console.log(`    [${normalizeSpeakerName(seg.speaker)}] ${seg.start.toFixed(2)}s - ${seg.end.toFixed(2)}s (${seg.wordCount} words)`);
    console.log(`      "${textPreview}"`);
  }

  // Score
  // Shift ground truth ranges to Whisper coordinate system too
  const alignedGT: GroundTruthRange[] = gtRanges.map(r => ({
    ...r,
    startSec: r.startSec + alignmentShift,
    endSec: r.endSec + alignmentShift,
  }));

  const scores = scoreAttribution(attributed, alignedGT);
  printScores('PART A RESULTS: Speaker-mapper accuracy (real caption boundaries)', scores);
}

// ── Part B: Full audio replay with Whisper ──────────────────────────

async function runPartB(): Promise<void> {
  console.log('\n\n  ========================================================');
  console.log('  PART B: Full pipeline replay (audio + captions + Whisper)');
  console.log('  SpeakerStreamManager -> Whisper -> speaker-mapper');
  console.log('  ========================================================\n');

  // ── 1. Load reference data ──────────────────────────────────────

  const data = loadReferenceData();
  const s1 = session1Events(data);
  const captionEvents = extractCaptionEvents(s1);

  console.log(`  Caption events: ${captionEvents.length}`);

  // ── 2. Build mixed audio from TTS files ─────────────────────────
  //
  // Timeline: Ground truth send times tell us when each speaker starts.
  // We place each TTS file at the relative offset from SESSION1_START.
  // Gaps between turns are filled with silence.

  const turnAudio: { audio: Float32Array; offsetSec: number; speaker: string }[] = [];
  for (const turn of SESSION1_TURNS) {
    const audio = readWavAsFloat32(`${AUDIO_DIR}/${turn.audioFile}`);
    const offsetSec = turn.sendTime - SESSION1_START;
    turnAudio.push({ audio, offsetSec, speaker: turn.speaker });
    console.log(`  ${turn.speaker}: offset=${offsetSec.toFixed(1)}s, audio=${(audio.length / SAMPLE_RATE).toFixed(1)}s (${turn.audioFile})`);
  }

  // Total duration: last turn offset + its audio duration + 2s tail
  const lastTurn = turnAudio[turnAudio.length - 1];
  const totalDurSec = lastTurn.offsetSec + (lastTurn.audio.length / SAMPLE_RATE) + 2;
  const totalSamples = Math.ceil(totalDurSec * SAMPLE_RATE);
  const mixed = new Float32Array(totalSamples);

  // Place each turn's audio at its offset (mix/overlay)
  for (const t of turnAudio) {
    const startSample = Math.floor(t.offsetSec * SAMPLE_RATE);
    for (let i = 0; i < t.audio.length && (startSample + i) < mixed.length; i++) {
      mixed[startSample + i] += t.audio[i];
    }
  }

  // Clamp to [-1, 1]
  for (let i = 0; i < mixed.length; i++) {
    mixed[i] = Math.max(-1, Math.min(1, mixed[i]));
  }

  console.log(`\n  Mixed audio: ${totalDurSec.toFixed(1)}s (${totalSamples} samples)`);

  // ── 3. Build ground truth ranges ────────────────────────────────

  const gtRanges: GroundTruthRange[] = [];
  for (let i = 0; i < SESSION1_TURNS.length; i++) {
    const turn = SESSION1_TURNS[i];
    const ta = turnAudio[i];
    const audioDur = ta.audio.length / SAMPLE_RATE;
    gtRanges.push({
      speaker: turn.speaker,
      startSec: ta.offsetSec,
      endSec: ta.offsetSec + audioDur,
      text: turn.text,
    });
  }

  console.log(`\n  Ground truth ranges (audio-based):`);
  for (const r of gtRanges) {
    console.log(`    ${r.speaker}: ${r.startSec.toFixed(1)}s - ${r.endSec.toFixed(1)}s`);
  }

  // ── 4. Build speaker boundaries from real caption events ────────
  //
  // Caption timestamps are relative to the first event in the log file
  // (a DOM speaker_end at 0.0s). The first caption_text arrives at ~26.6s.
  // Our audio starts at 0.0s (first TTS placed at offset 0).
  // So we rebase: caption time - first_caption_time = audio time.

  const firstCaptionTime = captionEvents.length > 0 ? captionEvents[0].timestamp : 0;
  const rebasedCaptions = captionEvents.map(c => ({
    ...c,
    timestamp: c.timestamp - firstCaptionTime,
  }));

  console.log(`\n  Caption rebase: first caption at ${firstCaptionTime.toFixed(1)}s → shifted to 0.0s`);

  const boundaries = captionsToSpeakerBoundaries(rebasedCaptions);

  console.log(`\n  Speaker boundaries (from real captions):`);
  for (const b of boundaries) {
    console.log(`    ${normalizeSpeakerName(b.speaker)}: ${b.start.toFixed(2)}s - ${b.end.toFixed(2)}s`);
  }

  // ── 5. Feed audio through SpeakerStreamManager + Whisper ────────

  const txClient = new TranscriptionClient({
    serviceUrl: TX_URL,
    apiToken: TX_TOKEN,
    sampleRate: SAMPLE_RATE,
    maxSpeechDurationSec: 15,
  });

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 3,
    submitInterval: 3,
    confirmThreshold: 3,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
  });

  const t0 = Date.now();
  const ts = () => ((Date.now() - t0) / 1000).toFixed(1);
  const allWords: TimestampedWord[] = [];
  const confirmedTexts: string[] = [];
  let whisperCalls = 0;
  let totalWhisperMs = 0;

  mgr.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
    whisperCalls++;
    const callNum = whisperCalls;
    const durSec = audioBuffer.length / SAMPLE_RATE;
    const start = Date.now();

    try {
      const result = await txClient.transcribe(audioBuffer);
      const elapsed = Date.now() - start;
      totalWhisperMs += elapsed;

      if (result?.text) {
        const text = result.text.trim();

        // Quality gate: match production filters
        if (result.segments && result.segments.length > 0) {
          const seg = result.segments[0];
          const noSpeech = seg.no_speech_prob ?? 0;
          const logProb = seg.avg_logprob ?? 0;
          const compression = seg.compression_ratio ?? 1;
          const segDuration = (seg.end || 0) - (seg.start || 0);

          if (noSpeech > 0.5 && logProb < -0.7) {
            console.log(`  [${ts()}s] [FILTERED] NO_SPEECH no_speech=${noSpeech.toFixed(2)} logprob=${logProb.toFixed(2)} | "${text}"`);
            mgr.handleTranscriptionResult(speakerId, '');
            return;
          }
          if (logProb < -0.8 && segDuration < 2.0) {
            console.log(`  [${ts()}s] [FILTERED] SHORT_GARBAGE logprob=${logProb.toFixed(2)} dur=${segDuration.toFixed(1)}s | "${text}"`);
            mgr.handleTranscriptionResult(speakerId, '');
            return;
          }
          if (compression > 2.4) {
            console.log(`  [${ts()}s] [FILTERED] REPETITIVE compression=${compression.toFixed(1)} | "${text}"`);
            mgr.handleTranscriptionResult(speakerId, '');
            return;
          }
        }

        const words = result.segments?.flatMap(s => s.words || []) || [];
        const textPreview = text.length > 80 ? text.substring(0, 80) + '...' : text;
        console.log(`  [${ts()}s] WHISPER #${callNum} | ${elapsed}ms | ${durSec.toFixed(1)}s audio | ${words.length} words | "${textPreview}"`);

        // Accumulate all word timestamps (replace on each call since sliding window re-transcribes)
        if (words.length > 0) {
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
    confirmedTexts.push(text);
    console.log(`\n  CONFIRMED | "${text.substring(0, 80)}${text.length > 80 ? '...' : ''}"\n`);
  };

  // Schedule caption events to fire at their relative timestamps
  // (same timing as the real meeting, but driven by setTimeout from playback start)
  const captionLog: { speaker: string; timestamp: number }[] = [];
  const captionTimers: ReturnType<typeof setTimeout>[] = [];

  for (const ce of rebasedCaptions) {
    const delayMs = Math.max(0, ce.timestamp * 1000); // rebased timestamp -> ms
    const timer = setTimeout(() => {
      captionLog.push({ speaker: ce.speaker, timestamp: ce.timestamp });
    }, delayMs);
    captionTimers.push(timer);
  }

  // Feed audio at real-time speed
  console.log(`\n  [0.0s] Playing ${totalDurSec.toFixed(1)}s of mixed audio at real-time speed...\n`);
  mgr.addSpeaker('mixed', 'Mixed');

  const totalChunks = Math.ceil(mixed.length / CHUNK_SIZE);
  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, mixed.length);
    mgr.feedAudio('mixed', mixed.subarray(start, end));

    const audioSec = (i * CHUNK_SIZE) / SAMPLE_RATE;
    if (i > 0 && Math.floor(audioSec) % 10 === 0 && Math.floor(((i - 1) * CHUNK_SIZE) / SAMPLE_RATE) % 10 !== 0) {
      console.log(`  [${ts()}s] --- ${audioSec.toFixed(0)}s / ${totalDurSec.toFixed(0)}s audio played ---`);
    }

    await new Promise(r => setTimeout(r, CHUNK_DURATION_MS));
  }

  console.log(`\n  [${ts()}s] Audio playback complete. Waiting for final transcription...\n`);
  await new Promise(r => setTimeout(r, 3000));
  mgr.flushSpeaker('mixed', true);
  await new Promise(r => setTimeout(r, 3000));

  // Clear caption timers
  for (const t of captionTimers) clearTimeout(t);

  // ── 6. Run speaker attribution ──────────────────────────────────

  console.log('  --------------------------------------------------------');
  console.log('  SPEAKER ATTRIBUTION');
  console.log('  --------------------------------------------------------\n');

  if (allWords.length === 0) {
    console.log('  ERROR: No word timestamps from Whisper. Cannot attribute speakers.');
    console.log('  Check that transcription-service is running at', TX_URL);
    mgr.removeAll();
    process.exit(1);
  }

  console.log(`  ${allWords.length} words from Whisper, ${boundaries.length} speaker boundaries`);

  const attributed = mapWordsToSpeakers(allWords, boundaries);

  console.log(`\n  Attributed segments:`);
  for (const seg of attributed) {
    const textPreview = seg.text.length > 70 ? seg.text.substring(0, 70) + '...' : seg.text;
    console.log(`    [${normalizeSpeakerName(seg.speaker)}] ${seg.start.toFixed(2)}s - ${seg.end.toFixed(2)}s (${seg.wordCount} words)`);
    console.log(`      "${textPreview}"`);
  }

  // ── 7. Score per-word attribution ───────────────────────────────

  const scores = scoreAttribution(attributed, gtRanges);
  printScores('PART B RESULTS: Full pipeline attribution accuracy', scores);

  // ── 8. Summary ──────────────────────────────────────────────────

  const wallTimeSec = (Date.now() - t0) / 1000;
  const rtf = totalWhisperMs / 1000 / totalDurSec;

  console.log('\n  --------------------------------------------------------');
  console.log('  SUMMARY');
  console.log('  --------------------------------------------------------\n');
  console.log(`  Speakers:         2 (Alice, Bob) -- 4 turns`);
  console.log(`  Audio duration:   ${totalDurSec.toFixed(1)}s`);
  console.log(`  Wall time:        ${wallTimeSec.toFixed(1)}s`);
  console.log(`  Whisper calls:    ${whisperCalls} (avg ${whisperCalls > 0 ? (totalWhisperMs / whisperCalls).toFixed(0) : 0}ms)`);
  console.log(`  RTF:              ${rtf.toFixed(2)}x`);
  console.log(`  Words from Whisper: ${allWords.length}`);
  console.log(`  Confirmed texts:  ${confirmedTexts.length}`);
  console.log(`  Caption events:   ${captionEvents.length} (replayed from real meeting)`);
  console.log(`  Boundaries:       ${boundaries.length}`);
  console.log(`  Attribution:      ${scores.overall.pct}% correct\n`);

  // Word-level detail
  if (allWords.length > 0) {
    console.log('  Word timestamps:');
    const wrap = (text: string, prefix: string, width: number = 80): void => {
      const tokens = text.split(' ');
      let line = prefix;
      for (const w of tokens) {
        if (line.length + w.length + 1 > width) {
          console.log(line);
          line = prefix + w;
        } else {
          line += (line.length > prefix.length ? ' ' : '') + w;
        }
      }
      if (line.length > prefix.length) console.log(line);
    };
    const wordLine = allWords.map(w => `${w.word.trim()}[${w.start.toFixed(1)}s]`).join(' ');
    wrap(wordLine, '    ');
  }

  console.log('');
  mgr.removeAll();
}

// ── Main ────────────────────────────────────────────────────────────

async function main() {
  console.log('\n  ============================================================');
  console.log('  REPLAY MEETING TEST: Real Teams meeting data');
  console.log('  Session 1: Alice/Bob 4-turn conversation');
  console.log('  Reference: reference-timestamped-data.json + ground truth');
  console.log('  ============================================================');

  // Part A: instant, no Whisper needed
  runPartA();

  // Part B: full pipeline, needs Whisper
  const skipPartB = process.argv.includes('--part-a-only');
  if (skipPartB) {
    console.log('\n  Skipping Part B (--part-a-only flag set)\n');
  } else {
    await runPartB();
  }

  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e); process.exit(1); });
