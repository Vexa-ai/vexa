/**
 * Integration test: SpeakerStreamManager + real TranscriptionClient + real Whisper.
 *
 * Generates TTS-like audio (sine wave speech simulation), feeds it through the
 * full pipeline, and logs every event: submissions, Whisper results, confirmations,
 * offset advances, and final segments.
 *
 * Run: npx ts-node services/vexa-bot/core/src/services/speaker-streams.integration-test.ts
 *
 * Requires: transcription-service running on localhost:8085
 */

import { SpeakerStreamManager } from './speaker-streams';
import { TranscriptionClient } from './transcription-client';

const SAMPLE_RATE = 16000;
const CHUNK_SIZE = 4096;
const TX_URL = process.env.TRANSCRIPTION_URL || 'http://localhost:8085/v1/audio/transcriptions';
const TX_TOKEN = process.env.TRANSCRIPTION_TOKEN || '32c59b9f654f1b6e376c6f020d79897d';

// Generate speech-like audio: mix of sine waves at speech frequencies
function makeSpeechChunk(): Float32Array {
  const chunk = new Float32Array(CHUNK_SIZE);
  for (let i = 0; i < CHUNK_SIZE; i++) {
    // Mix fundamentals + harmonics to sound more like speech
    chunk[i] = (
      Math.sin(2 * Math.PI * 150 * i / SAMPLE_RATE) * 0.3 +
      Math.sin(2 * Math.PI * 300 * i / SAMPLE_RATE) * 0.2 +
      Math.sin(2 * Math.PI * 450 * i / SAMPLE_RATE) * 0.1 +
      Math.random() * 0.1 // noise
    );
  }
  return chunk;
}

function makeSilenceChunk(): Float32Array {
  return new Float32Array(CHUNK_SIZE); // zeros
}

async function sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms));
}

async function main() {
  console.log('=== SpeakerStreamManager Integration Test ===');
  console.log(`Transcription URL: ${TX_URL}`);
  console.log(`Sample rate: ${SAMPLE_RATE}, Chunk size: ${CHUNK_SIZE}`);
  console.log('');

  const txClient = new TranscriptionClient({
    serviceUrl: TX_URL,
    apiToken: TX_TOKEN,
    sampleRate: SAMPLE_RATE,
  });

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 2,
    submitInterval: 2,
    confirmThreshold: 2,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
  });

  // Track events
  const events: { time: number; type: string; detail: string }[] = [];
  const t0 = Date.now();
  const log = (type: string, detail: string) => {
    const t = ((Date.now() - t0) / 1000).toFixed(1);
    events.push({ time: Date.now() - t0, type, detail });
    console.log(`[${t}s] [${type}] ${detail}`);
  };

  let whisperCalls = 0;
  let confirmedSegments: { speaker: string; text: string; start: number; end: number }[] = [];

  mgr.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
    whisperCalls++;
    const durSec = (audioBuffer.length / SAMPLE_RATE).toFixed(1);
    log('SUBMIT', `${speakerName} | ${durSec}s audio | call #${whisperCalls}`);

    try {
      const result = await txClient.transcribe(audioBuffer);
      if (result && result.text) {
        log('WHISPER', `${speakerName} | "${result.text}" | lang=${result.language}`);
        mgr.handleTranscriptionResult(speakerId, result.text);
      } else {
        log('WHISPER', `${speakerName} | (empty result)`);
        mgr.handleTranscriptionResult(speakerId, '');
      }
    } catch (err: any) {
      log('ERROR', `${speakerName} | ${err.message}`);
      mgr.handleTranscriptionResult(speakerId, '');
    }
  };

  mgr.onSegmentConfirmed = (speakerId, speakerName, transcript, startMs, endMs, segmentId) => {
    const startSec = ((startMs - t0) / 1000).toFixed(1);
    const endSec = ((endMs - t0) / 1000).toFixed(1);
    confirmedSegments.push({ speaker: speakerName, text: transcript, start: startMs - t0, end: endMs - t0 });
    log('CONFIRMED', `${speakerName} | ${startSec}s-${endSec}s | "${transcript}"`);
  };

  // === Scenario 1: Single speaker, 10s of audio ===
  console.log('\n--- Scenario 1: Single speaker, 10s continuous ---');
  mgr.addSpeaker('s1', 'Speaker-A');

  // Feed 10s of audio, triggering submissions along the way
  const chunksFor10s = Math.ceil(10 * SAMPLE_RATE / CHUNK_SIZE);
  for (let i = 0; i < chunksFor10s; i++) {
    mgr.feedAudio('s1', makeSpeechChunk());
    // Every ~2s worth of chunks, trigger a submit
    if ((i + 1) % Math.ceil(2 * SAMPLE_RATE / CHUNK_SIZE) === 0) {
      // @ts-ignore
      mgr['trySubmit']('s1');
      await sleep(500); // give Whisper time
    }
  }

  // Final submit
  // @ts-ignore
  mgr['trySubmit']('s1');
  await sleep(1000);

  // Flush (speaker change)
  log('FLUSH', 'Speaker-A — simulating speaker change');
  mgr.flushSpeaker('s1');
  await sleep(1000);

  // === Scenario 2: Two speakers, alternating ===
  console.log('\n--- Scenario 2: Two speakers, alternating ---');
  mgr.addSpeaker('s2', 'Speaker-B');

  // Speaker-A talks 4s
  for (let i = 0; i < Math.ceil(4 * SAMPLE_RATE / CHUNK_SIZE); i++) {
    mgr.feedAudio('s1', makeSpeechChunk());
  }
  // @ts-ignore
  mgr['trySubmit']('s1');
  await sleep(500);
  // @ts-ignore
  mgr['trySubmit']('s1');
  await sleep(500);

  // Speaker change: flush A, start B
  log('FLUSH', 'Speaker-A → Speaker-B transition');
  mgr.flushSpeaker('s1');

  // Speaker-B talks 4s
  for (let i = 0; i < Math.ceil(4 * SAMPLE_RATE / CHUNK_SIZE); i++) {
    mgr.feedAudio('s2', makeSpeechChunk());
  }
  // @ts-ignore
  mgr['trySubmit']('s2');
  await sleep(500);
  // @ts-ignore
  mgr['trySubmit']('s2');
  await sleep(500);

  log('FLUSH', 'Speaker-B — end');
  mgr.flushSpeaker('s2');
  await sleep(500);

  // === Summary ===
  console.log('\n=== SUMMARY ===');
  console.log(`Whisper calls: ${whisperCalls}`);
  console.log(`Confirmed segments: ${confirmedSegments.length}`);
  confirmedSegments.forEach((s, i) => {
    console.log(`  ${i + 1}. [${s.speaker}] "${s.text}"`);
  });

  console.log(`\nEvents timeline:`);
  events.forEach(e => {
    console.log(`  ${(e.time / 1000).toFixed(1)}s | ${e.type.padEnd(10)} | ${e.detail}`);
  });

  mgr.removeAll();
  process.exit(0);
}

main().catch(e => {
  console.error('Fatal:', e.message);
  process.exit(1);
});
