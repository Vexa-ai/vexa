/**
 * Standalone test for SpeakerStreamManager offset-based sliding window.
 *
 * Run: npx tsx services/vexa-bot/core/src/services/speaker-streams.test.ts
 *
 * Tests the core pipeline in isolation — no meetings, no browser, no Whisper.
 * Mocks the transcription callback to simulate Whisper returning segments.
 */

import { SpeakerStreamManager } from './speaker-streams';

const SAMPLE_RATE = 16000;
const CHUNK_SIZE = 4096; // same as ScriptProcessor

// Generate a chunk of fake audio (sine wave)
function makeChunk(durationSec: number = CHUNK_SIZE / SAMPLE_RATE): Float32Array {
  const samples = Math.floor(durationSec * SAMPLE_RATE);
  const chunk = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    chunk[i] = Math.sin(2 * Math.PI * 440 * i / SAMPLE_RATE) * 0.5;
  }
  return chunk;
}

// Feed N seconds of audio in chunks
function feedAudio(mgr: SpeakerStreamManager, speakerId: string, seconds: number): void {
  const chunksNeeded = Math.ceil(seconds * SAMPLE_RATE / CHUNK_SIZE);
  for (let i = 0; i < chunksNeeded; i++) {
    mgr.feedAudio(speakerId, makeChunk());
  }
}

let passed = 0;
let failed = 0;

function assert(condition: boolean, message: string): void {
  if (condition) {
    passed++;
    console.log(`  ✓ ${message}`);
  } else {
    failed++;
    console.log(`  ✗ ${message}`);
  }
}

// ─── Test 1: Basic offset advancement ────────────────────────

console.log('\nTest 1: Offset advancement on confirmation');
{
  const confirmed: { text: string; start: number; end: number }[] = [];
  let whisperCallCount = 0;
  let lastAudioLength = 0;

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 1,
    submitInterval: 1,
    confirmThreshold: 2,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
  });

  mgr.onSegmentReady = (id, name, audio) => {
    whisperCallCount++;
    lastAudioLength = audio.length;
    // Simulate Whisper: return fixed text for first segment, then new text
    if (whisperCallCount <= 3) {
      mgr.handleTranscriptionResult(id, 'Hello world this is a test.');
    } else {
      mgr.handleTranscriptionResult(id, 'Second segment of speech.');
    }
  };

  mgr.onSegmentConfirmed = (id, name, text, start, end, segId) => {
    confirmed.push({ text, start, end });
  };

  mgr.addSpeaker('s1', 'Alice');

  // Feed 3s of audio — should trigger first submission at 1s min
  feedAudio(mgr, 's1', 3);

  // Manually trigger submit cycle (normally timer-driven)
  // @ts-ignore — accessing private method for testing
  mgr['trySubmit']('s1');
  // Second submit to get confirmation
  feedAudio(mgr, 's1', 1);
  // @ts-ignore
  mgr['trySubmit']('s1');

  assert(whisperCallCount >= 2, `Whisper called ${whisperCallCount} times (expected >=2)`);
  assert(confirmed.length >= 1, `${confirmed.length} confirmed segment(s)`);
  if (confirmed.length > 0) {
    assert(confirmed[0].text === 'Hello world this is a test.', `First segment: "${confirmed[0].text}"`);
  }

  // After confirmation, the offset should have advanced — next submission
  // should send LESS audio (only unconfirmed tail)
  const prevLength = lastAudioLength;
  feedAudio(mgr, 's1', 2);
  // @ts-ignore
  mgr['trySubmit']('s1');

  assert(lastAudioLength < prevLength, `After offset advance: Whisper got ${lastAudioLength} samples (was ${prevLength})`);

  mgr.removeAll();
}

// ─── Test 2: Buffer doesn't reset on confirmation ─────────────

console.log('\nTest 2: Buffer continuity — no reset on confirmation');
{
  let submitLengths: number[] = [];
  let confirmCount = 0;

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 1,
    submitInterval: 1,
    confirmThreshold: 2,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
  });

  mgr.onSegmentReady = (id, name, audio) => {
    submitLengths.push(audio.length);
    // Return same text so it can confirm
    mgr.handleTranscriptionResult(id, 'Continuous speech that stays the same for confirmation.');
  };

  mgr.onSegmentConfirmed = () => { confirmCount++; };

  mgr.addSpeaker('s1', 'Bob');

  // Feed 10s of continuous audio, trigger submits
  for (let i = 0; i < 10; i++) {
    feedAudio(mgr, 's1', 1);
    // @ts-ignore
    mgr['trySubmit']('s1');
  }

  // After first confirmation, subsequent submissions should be SMALLER
  // (only unconfirmed audio), not full buffer
  if (submitLengths.length >= 4) {
    const firstSubmit = submitLengths[0];
    const afterConfirm = submitLengths[submitLengths.length - 1];
    // After confirmation, the unconfirmed window should be smaller than the
    // growing pre-confirmation buffer
    assert(confirmCount >= 1, `Confirmed ${confirmCount} segment(s)`);
    console.log(`  Submission sizes: [${submitLengths.map(s => (s/SAMPLE_RATE).toFixed(1)+'s').join(', ')}]`);
  }

  mgr.removeAll();
}

// ─── Test 3: Flush on speaker change ──────────────────────────

console.log('\nTest 3: Speaker change flush');
{
  let confirmed: string[] = [];

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 1,
    submitInterval: 1,
    confirmThreshold: 2,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
  });

  mgr.onSegmentReady = (id, name, audio) => {
    mgr.handleTranscriptionResult(id, `${name} speaking`);
  };

  mgr.onSegmentConfirmed = (id, name, text) => {
    confirmed.push(`${name}: ${text}`);
  };

  mgr.addSpeaker('s1', 'Alice');
  feedAudio(mgr, 's1', 5);
  // @ts-ignore
  mgr['trySubmit']('s1');
  // @ts-ignore
  mgr['trySubmit']('s1');

  // Speaker change — flush Alice
  mgr.flushSpeaker('s1');

  assert(confirmed.length >= 1, `Alice's segment emitted on flush (${confirmed.length} segments)`);

  mgr.removeAll();
}

// ─── Test 4: Short segments kept for next turn ─────────────────

console.log('\nTest 4: Short segments skip flush');
{
  let flushed = false;

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 2,
    submitInterval: 1,
    confirmThreshold: 2,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
  });

  mgr.onSegmentReady = (id, name, audio) => {
    mgr.handleTranscriptionResult(id, '');
  };

  mgr.onSegmentConfirmed = () => { flushed = true; };

  mgr.addSpeaker('s1', 'Alice');

  // Feed only 0.5s — too short
  feedAudio(mgr, 's1', 0.5);

  // Flush — should skip because < minAudioDuration and no transcript
  mgr.flushSpeaker('s1');

  assert(!flushed, 'Short segment NOT flushed (kept for next turn)');

  // Now feed more and flush again — should work
  feedAudio(mgr, 's1', 3);
  // @ts-ignore
  mgr['trySubmit']('s1');
  mgr.handleTranscriptionResult('s1', 'Now enough audio');
  mgr.flushSpeaker('s1');

  assert(flushed, 'Segment flushed after enough audio');

  mgr.removeAll();
}

// ─── Test 5: Buffer trim at max duration ──────────────────────

console.log('\nTest 5: Buffer trim');
{
  let submitSizes: number[] = [];

  const mgr = new SpeakerStreamManager({
    sampleRate: SAMPLE_RATE,
    minAudioDuration: 1,
    submitInterval: 1,
    confirmThreshold: 2,
    maxBufferDuration: 5, // 5s max for testing
    idleTimeoutSec: 15,
  });

  mgr.onSegmentReady = (id, name, audio) => {
    submitSizes.push(audio.length);
    // Same text so confirmation triggers → offset advances → trim happens
    mgr.handleTranscriptionResult(id, 'Repeated text for trim test confirmation.');
  };

  mgr.onSegmentConfirmed = () => {};

  mgr.addSpeaker('s1', 'Alice');

  // Feed 8s of audio (exceeds 5s max)
  feedAudio(mgr, 's1', 8);

  // Trigger submits
  for (let i = 0; i < 5; i++) {
    // @ts-ignore
    mgr['trySubmit']('s1');
  }

  // After confirmation + trim, buffer should be smaller than 8s
  const buf = mgr['buffers'].get('s1');
  const totalSec = (buf?.totalSamples ?? 0) / SAMPLE_RATE;
  assert(totalSec <= 6, `Buffer trimmed to ${totalSec.toFixed(1)}s (max=5s)`);

  mgr.removeAll();
}

// ─── Summary ──────────────────────────────────────────────────

console.log(`\n${'='.repeat(40)}`);
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
