/**
 * Pipeline test: feeds a WAV file at real-time speed through
 * SpeakerStreamManager → TranscriptionClient → Whisper.
 *
 * Audio is fed chunk-by-chunk at the actual sample rate (~256ms per chunk).
 * The SpeakerStreamManager's internal 2s timer drives submissions — same
 * as production. Play audio alongside with `make play`.
 *
 * Usage: npx ts-node core/src/services/speaker-streams.wav-test.ts [wav-file]
 */

import * as fs from 'fs';
import { SpeakerStreamManager } from './speaker-streams';
import { TranscriptionClient } from './transcription-client';
import { SileroVAD } from './vad';

const SAMPLE_RATE = 16000;
const CHUNK_SIZE = 4096;
const CHUNK_DURATION_MS = (CHUNK_SIZE / SAMPLE_RATE) * 1000; // ~256ms
const WAV_PATH = process.argv[2] || '/tmp/test-speech.wav';
const TX_URL = process.env.TRANSCRIPTION_URL || 'http://localhost:8085/v1/audio/transcriptions';
const TX_TOKEN = process.env.TRANSCRIPTION_TOKEN || '32c59b9f654f1b6e376c6f020d79897d';

function readWavAsFloat32(path: string): Float32Array {
  const buf = fs.readFileSync(path);
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error('Not a WAV file');

  const sampleRate = buf.readUInt32LE(24);
  const bitsPerSample = buf.readUInt16LE(34);
  const numChannels = buf.readUInt16LE(22);

  let dataOffset = 36;
  while (dataOffset < buf.length - 8) {
    if (buf.toString('ascii', dataOffset, dataOffset + 4) === 'data') {
      dataOffset += 8;
      break;
    }
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

async function main() {
  const audio = readWavAsFloat32(WAV_PATH);
  const totalDuration = audio.length / SAMPLE_RATE;
  const totalChunks = Math.ceil(audio.length / CHUNK_SIZE);

  console.log(`\n  File:     ${WAV_PATH}`);
  console.log(`  Duration: ${totalDuration.toFixed(1)}s (${totalChunks} chunks at ${CHUNK_DURATION_MS.toFixed(0)}ms each)`);
  console.log(`  Whisper:  ${TX_URL}\n`);

  const txClient = new TranscriptionClient({
    serviceUrl: TX_URL,
    apiToken: TX_TOKEN,
    sampleRate: SAMPLE_RATE,
    maxSpeechDurationSec: 15,
  });

  // Load Silero VAD if VAD=1 is set (test silence rejection at entry gate)
  const useVad = process.env.VAD === '1';
  let vadModel: SileroVAD | null = null;
  let vadState: import('./vad').VadSpeakerState | null = null;
  if (useVad) {
    try {
      vadModel = await SileroVAD.create();
      vadState = vadModel.createSpeakerState();
      console.log('  VAD:      Silero streaming enabled (per-chunk gate)\n');
    } catch (err: any) {
      console.log(`  VAD:      failed to load (${err.message}), disabled\n`);
    }
  }

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
  const audioTs = (chunkIdx: number) => ((chunkIdx * CHUNK_SIZE) / SAMPLE_RATE).toFixed(1);

  let whisperCalls = 0;
  let totalWhisperMs = 0;
  let totalAudioSentSec = 0; // cumulative audio duration sent to Whisper
  let filteredCount = 0;
  const confirmed: string[] = [];
  let firstConfirmWallMs = 0; // wall time of first confirmation
  let lastDraft = '';
  let latestWords: {word: string; start: number; end: number; probability: number}[] = [];
  const allWords: typeof latestWords = [];
  mgr.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
    whisperCalls++;
    const durSec = audioBuffer.length / SAMPLE_RATE;
    totalAudioSentSec += durSec;

    const start = Date.now();
    try {
      const result = await txClient.transcribe(audioBuffer);
      const elapsed = Date.now() - start;
      totalWhisperMs += elapsed;

      if (result?.text) {
        const text = result.text.trim();

        // ── Quality gate: match production filters (index.ts:1044-1090) ──
        if (result.segments && result.segments.length > 0) {
          const seg = result.segments[0];
          const noSpeech = seg.no_speech_prob ?? 0;
          const logProb = seg.avg_logprob ?? 0;
          const compression = seg.compression_ratio ?? 1;
          const segDuration = (seg.end || 0) - (seg.start || 0);

          // High no_speech_prob + low logprob = noise, not speech
          if (noSpeech > 0.5 && logProb < -0.7) {
            filteredCount++;
            console.log(`  [${ts()}s] [FILTERED] NO_SPEECH no_speech=${noSpeech.toFixed(2)} logprob=${logProb.toFixed(2)} | "${text}"`);
            mgr.handleTranscriptionResult(speakerId, '');
            return;
          }

          // Very low logprob on short audio = garbage
          if (logProb < -0.8 && segDuration < 2.0) {
            filteredCount++;
            console.log(`  [${ts()}s] [FILTERED] SHORT_GARBAGE logprob=${logProb.toFixed(2)} dur=${segDuration.toFixed(1)}s | "${text}"`);
            mgr.handleTranscriptionResult(speakerId, '');
            return;
          }

          // High compression ratio = repetitive output (hallucination pattern)
          if (compression > 2.4) {
            filteredCount++;
            console.log(`  [${ts()}s] [FILTERED] REPETITIVE compression=${compression.toFixed(1)} | "${text}"`);
            mgr.handleTranscriptionResult(speakerId, '');
            return;
          }

          // Flag low-probability words (informational, not filtered)
          const words = result.segments.flatMap(s => s.words || []);
          const lowProbWords = words.filter(w => (w.probability ?? 1) < 0.3);
          if (lowProbWords.length > 0) {
            const flagged = lowProbWords.map(w => `${w.word?.trim()}(${(w.probability ?? 0).toFixed(2)})`).join(', ');
            console.log(`  [${ts()}s] [LOW_PROB] ${lowProbWords.length} words: ${flagged}`);
          }
        }

        const words = result.segments?.flatMap(s => s.words || []) || [];
        if (text !== lastDraft) {
          const wordCount = words.length > 0 ? ` | ${words.length} words` : '';
          console.log(`  [${ts()}s] DRAFT  | ${elapsed}ms${wordCount} | "${text}"`);
          lastDraft = text;
        }
        // Store latest words for summary
        if (words.length > 0) latestWords = words;
        // Pass Whisper's last segment end time for precise offset clipping
        const lastSeg = result.segments?.[result.segments.length - 1];
        const whisperSegs = result.segments?.map(s => ({ text: s.text, start: s.start, end: s.end }));
        mgr.handleTranscriptionResult(speakerId, text, lastSeg?.end, whisperSegs);
      } else {
        mgr.handleTranscriptionResult(speakerId, '');
      }
    } catch (err: any) {
      console.log(`  [${ts()}s] ERROR  | ${err.message}`);
      mgr.handleTranscriptionResult(speakerId, '');
    }

  };

  mgr.onSegmentConfirmed = (speakerId, speakerName, text) => {
    confirmed.push(text);
    if (!firstConfirmWallMs) firstConfirmWallMs = Date.now() - t0;
    if (latestWords.length > 0) {
      allWords.push(...latestWords);
      latestWords = [];
    }
    console.log(`\n  ✓ [${ts()}s] CONFIRMED | "${text}"\n`);
  };

  // Feed audio at real-time speed. Timer drives submissions.
  console.log(`  [0.0s] ▶ Playing ${totalDuration.toFixed(1)}s of audio...\n`);
  mgr.addSpeaker('s1', 'Speaker');

  let vadSkipCount = 0;
  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, audio.length);
    const chunk = audio.subarray(start, end);

    // VAD gate at entry (same as production handlePerSpeakerAudioData)
    if (vadModel && vadState && chunk.length >= 512) {
      const isSpeech = await vadModel.isSpeechStreaming(chunk, vadState);
      if (!isSpeech) {
        vadSkipCount++;
        // Don't feed — simulates ambient noise rejection
        await new Promise(r => setTimeout(r, CHUNK_DURATION_MS));
        continue;
      }
    }

    mgr.feedAudio('s1', chunk);

    const audioSec = (i * CHUNK_SIZE) / SAMPLE_RATE;
    if (i > 0 && Math.floor(audioSec) % 5 === 0 && Math.floor(((i - 1) * CHUNK_SIZE) / SAMPLE_RATE) % 5 !== 0) {
      console.log(`  [${ts()}s] ░░░ ${audioSec.toFixed(0)}s / ${totalDuration.toFixed(0)}s audio played ░░░`);
    }

    await new Promise(r => setTimeout(r, CHUNK_DURATION_MS));
  }

  // Wait for in-flight Whisper calls
  await new Promise(r => setTimeout(r, 3000));

  console.log(`  [${ts()}s] ■ Complete. Flushing remaining buffer...\n`);
  mgr.flushSpeaker('s1', true); // force=true — end of stream, process regardless of min duration
  await new Promise(r => setTimeout(r, 2000));

  // Deduplicate and join confirmed segments into clean output.
  // Segments may overlap at boundaries — Whisper sometimes repeats the
  // last few words of the previous segment at the start of the next.
  function deduplicateSegments(segments: string[]): string {
    if (segments.length === 0) return '';
    let result = segments[0];
    for (let i = 1; i < segments.length; i++) {
      const prev = result;
      const next = segments[i];
      // Find overlap: check if the end of prev matches the start of next
      const prevWords = prev.split(/\s+/);
      const nextWords = next.split(/\s+/);
      let bestOverlap = 0;
      // Try matching last N words of prev with first N words of next
      for (let n = Math.min(8, prevWords.length, nextWords.length); n >= 2; n--) {
        const prevTail = prevWords.slice(-n).join(' ').toLowerCase();
        const nextHead = nextWords.slice(0, n).join(' ').toLowerCase();
        if (prevTail === nextHead) {
          bestOverlap = n;
          break;
        }
      }
      if (bestOverlap > 0) {
        // Skip overlapping words from next
        result = result + ' ' + nextWords.slice(bestOverlap).join(' ');
      } else {
        result = result + ' ' + next;
      }
    }
    return result.replace(/\s+/g, ' ').trim();
  }

  const fullTranscript = deduplicateSegments(confirmed);

  // Load ground truth if .txt file exists alongside .wav
  const gtPath = WAV_PATH.replace(/\.wav$/i, '.txt');
  let groundTruth = '';
  try { groundTruth = fs.readFileSync(gtPath, 'utf8').trim(); } catch {}

  // Word-wrap helper
  const wrap = (text: string, prefix: string, width: number = 75): void => {
    const words = text.split(' ');
    let line = prefix;
    for (const w of words) {
      if (line.length + w.length + 1 > width) {
        console.log(line);
        line = prefix + w;
      } else {
        line += (line.length > prefix.length ? ' ' : '') + w;
      }
    }
    if (line.length > prefix.length) console.log(line);
  };

  // Metrics
  const wallTimeSec = (Date.now() - t0) / 1000;
  const rtf = totalWhisperMs / 1000 / totalDuration; // Whisper processing time / audio duration
  const audioReprocessFactor = totalAudioSentSec / totalDuration; // how many times audio was sent
  const firstConfirmLatency = firstConfirmWallMs ? (firstConfirmWallMs / 1000).toFixed(1) : 'N/A';

  // Summary
  console.log(`  ┌─────────────────────────────────────────────────`);
  console.log(`  │ Audio:      ${totalDuration.toFixed(1)}s`);
  console.log(`  │ Wall time:  ${wallTimeSec.toFixed(1)}s`);
  console.log(`  │ Whisper:    ${whisperCalls} calls, avg ${whisperCalls > 0 ? (totalWhisperMs / whisperCalls).toFixed(0) : 0}ms`);
  console.log(`  │ Filtered:   ${filteredCount} (quality gate rejections)`);
  console.log(`  │ VAD skips:  ${vadSkipCount} (chunks rejected by Silero streaming VAD at entry)`);
  console.log(`  │ Segments:   ${confirmed.length}`);
  console.log(`  │`);
  console.log(`  │ PERFORMANCE:`);
  console.log(`  │  RTF (Real-Time Factor):  ${rtf.toFixed(2)}x (Whisper time / audio time, <1 = real-time capable)`);
  console.log(`  │  Audio reprocess factor:  ${audioReprocessFactor.toFixed(1)}x (total audio sent / audio duration)`);
  console.log(`  │  First confirm latency:   ${firstConfirmLatency}s (wall time to first confirmed segment)`);
  console.log(`  │  Total audio to Whisper:  ${totalAudioSentSec.toFixed(1)}s (cumulative across ${whisperCalls} calls)`);
  console.log(`  │`);
  console.log(`  │ SEGMENTS:`);
  confirmed.forEach((t, i) => console.log(`  │  ${i + 1}. "${t}"`));
  console.log(`  │`);
  if (groundTruth) {
    console.log(`  │ GROUND TRUTH (TTS input):`);
    wrap(groundTruth, '  │  ');
    console.log(`  │`);
  }
  console.log(`  │ PIPELINE OUTPUT:`);
  wrap(fullTranscript, '  │  ');

  // Word-level diff if ground truth available
  if (groundTruth) {
    console.log(`  │`);
    console.log(`  │ WORD DIFF:`);

    // Normalize both for comparison: lowercase, strip punctuation
    const normalize = (s: string) => s.toLowerCase().replace(/[.,!?;:'"()-]/g, '').replace(/\s+/g, ' ').trim();
    const gtNorm = normalize(groundTruth);
    const outNorm = normalize(fullTranscript);
    const gtWords = gtNorm.split(' ');
    const outWords = outNorm.split(' ');

    // Simple LCS-based diff
    const m = gtWords.length, n = outWords.length;
    const dp: number[][] = Array.from({length: m + 1}, () => Array(n + 1).fill(0));
    for (let i = 1; i <= m; i++)
      for (let j = 1; j <= n; j++)
        dp[i][j] = gtWords[i-1] === outWords[j-1] ? dp[i-1][j-1] + 1 : Math.max(dp[i-1][j], dp[i][j-1]);

    // Backtrack to get diff
    const diff: {type: 'match'|'missing'|'extra', word: string}[] = [];
    let i = m, j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && gtWords[i-1] === outWords[j-1]) {
        diff.unshift({type: 'match', word: gtWords[i-1]});
        i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j-1] >= dp[i-1][j])) {
        diff.unshift({type: 'extra', word: outWords[j-1]});
        j--;
      } else {
        diff.unshift({type: 'missing', word: gtWords[i-1]});
        i--;
      }
    }

    // Render diff with markers
    let line = '  │  ';
    let matches = 0, missing = 0, extra = 0;
    for (const d of diff) {
      let token: string;
      if (d.type === 'match') { token = d.word; matches++; }
      else if (d.type === 'missing') { token = `[-${d.word}-]`; missing++; }
      else { token = `{+${d.word}+}`; extra++; }

      if (line.length + token.length + 1 > 75) {
        console.log(line);
        line = '  │  ' + token;
      } else {
        line += (line.length > 5 ? ' ' : '') + token;
      }
    }
    if (line.length > 5) console.log(line);

    const total = matches + missing;
    const accuracy = total > 0 ? ((matches / total) * 100).toFixed(1) : '0';
    console.log(`  │`);
    console.log(`  │ ACCURACY: ${accuracy}% (${matches}/${total} words match, ${missing} missing, ${extra} extra)`);
  }

  if (allWords.length > 0) {
    console.log(`  │`);
    console.log(`  │ WORD TIMESTAMPS (${allWords.length} words):`);
    const wordLine = allWords.map(w => `${w.word.trim()}[${w.start.toFixed(2)}s]`).join(' ');
    wrap(wordLine, '  │  ');
  }

  console.log(`  └─────────────────────────────────────────────────\n`);

  mgr.removeAll();
  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e); process.exit(1); });
