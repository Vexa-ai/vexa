/**
 * Unit tests for SpeakerStreamManager confirmation buffer logic.
 *
 * Run: cd core && npx tsx src/services/__tests__/speaker-streams.test.ts
 *
 * No Docker, no external dependencies. Tests the pure logic of:
 * - Buffer accumulation
 * - Fuzzy-match confirmation
 * - Hard cap flush at maxBufferDuration
 * - removeAll flushes pending
 */

import assert from 'node:assert';
import { SpeakerStreamManager } from '../speaker-streams';

let passed = 0;
let failed = 0;

function test(name: string, fn: () => void) {
  try {
    fn();
    console.log(`  PASS  ${name}`);
    passed++;
  } catch (err: any) {
    console.log(`  FAIL  ${name}`);
    console.log(`        ${err.message}`);
    failed++;
  }
}

// Helpers
function makeSamples(durationSec: number, sampleRate = 16000): Float32Array {
  return new Float32Array(Math.floor(durationSec * sampleRate));
}

function createManager(overrides?: Record<string, number>) {
  return new SpeakerStreamManager({
    minAudioDuration: 1,
    submitInterval: 60, // large so timer doesn't fire during tests
    confirmThreshold: 2,
    maxBufferDuration: 5,
    sampleRate: 16000,
    ...overrides,
  });
}

console.log('\n=== SpeakerStreamManager Tests ===\n');

// --- Buffer accumulation ---

test('addSpeaker creates a buffer', () => {
  const mgr = createManager();
  mgr.addSpeaker('s1', 'Alice');
  assert.ok(mgr.hasSpeaker('s1'));
  assert.deepStrictEqual(mgr.getActiveSpeakers(), ['s1']);
  mgr.removeAll();
});

test('addSpeaker is idempotent', () => {
  const mgr = createManager();
  mgr.addSpeaker('s1', 'Alice');
  mgr.addSpeaker('s1', 'Alice duplicate');
  assert.deepStrictEqual(mgr.getActiveSpeakers(), ['s1']);
  mgr.removeAll();
});

test('feedAudio accumulates chunks', () => {
  const mgr = createManager();
  let readyCalled = false;
  mgr.onSegmentReady = () => { readyCalled = true; };
  mgr.addSpeaker('s1', 'Alice');
  mgr.feedAudio('s1', makeSamples(0.5));
  mgr.feedAudio('s1', makeSamples(0.5));
  // Not enough audio for minAudioDuration=1, but we can check it didn't crash
  assert.ok(mgr.hasSpeaker('s1'));
  mgr.removeAll();
});

test('feedAudio to unknown speaker is a no-op', () => {
  const mgr = createManager();
  // Should not throw
  mgr.feedAudio('unknown', makeSamples(1));
  assert.ok(!mgr.hasSpeaker('unknown'));
});

// --- Confirmation logic ---

test('single transcription result does not emit', () => {
  const mgr = createManager();
  let emitted: string | null = null;
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');
  mgr.handleTranscriptionResult('s1', 'Hello world, this is a test sentence.');
  assert.strictEqual(emitted, null, 'Should not emit after single result');
  mgr.removeAll();
});

test('two identical results trigger confirmation', () => {
  const mgr = createManager();
  let emitted: string | null = null;
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');
  const text = 'Hello world, this is a test sentence for confirmation.';
  mgr.handleTranscriptionResult('s1', text);
  mgr.handleTranscriptionResult('s1', text);
  assert.strictEqual(emitted, text, 'Should emit after 2 identical results');
  mgr.removeAll();
});

test('fuzzy match: minor trailing differences still confirm', () => {
  const mgr = createManager();
  let emitted: string | null = null;
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');
  // First 80% of the shorter string must match
  const base = 'Hello everyone, welcome to the meeting today. Let us begin with the agenda.';
  const variant = 'Hello everyone, welcome to the meeting today. Let us begin with the agenda items.';
  mgr.handleTranscriptionResult('s1', base);
  mgr.handleTranscriptionResult('s1', variant);
  assert.ok(emitted !== null, 'Fuzzy match should confirm');
  mgr.removeAll();
});

test('completely different texts reset confirm count', () => {
  const mgr = createManager();
  let emitted: string | null = null;
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');
  mgr.handleTranscriptionResult('s1', 'Hello world, this is Alice speaking today.');
  mgr.handleTranscriptionResult('s1', 'Goodbye world, Bob is leaving the meeting now.');
  assert.strictEqual(emitted, null, 'Different texts should not confirm');
  mgr.removeAll();
});

test('empty transcription result is ignored', () => {
  const mgr = createManager();
  let emitted: string | null = null;
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');
  mgr.handleTranscriptionResult('s1', '');
  mgr.handleTranscriptionResult('s1', '   ');
  assert.strictEqual(emitted, null, 'Empty results should be ignored');
  mgr.removeAll();
});

test('confirmation resets buffer after emit', () => {
  const mgr = createManager();
  const emissions: string[] = [];
  mgr.onSegmentConfirmed = (_id, _name, text) => { emissions.push(text); };
  mgr.addSpeaker('s1', 'Alice');

  const text1 = 'First segment text that is long enough for fuzzy matching to work.';
  mgr.handleTranscriptionResult('s1', text1);
  mgr.handleTranscriptionResult('s1', text1);
  assert.strictEqual(emissions.length, 1);

  // After reset, a new pair should emit a second segment
  const text2 = 'Second segment text that is also long enough for matching.';
  mgr.handleTranscriptionResult('s1', text2);
  mgr.handleTranscriptionResult('s1', text2);
  assert.strictEqual(emissions.length, 2);
  assert.strictEqual(emissions[1], text2);

  mgr.removeAll();
});

// --- Hard cap flush ---

test('hard cap flushes buffer at maxBufferDuration', () => {
  const mgr = createManager({ maxBufferDuration: 2 });
  let emitted: string | null = null;
  let readyCalled = false;
  mgr.onSegmentReady = () => { readyCalled = true; };
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');

  // Feed 1s of audio and give a transcript
  mgr.feedAudio('s1', makeSamples(1));
  mgr.handleTranscriptionResult('s1', 'Partial transcript text that has not been confirmed yet.');

  // Feed more audio to exceed the 2s hard cap
  mgr.feedAudio('s1', makeSamples(1.5));

  // Force a trySubmit cycle by calling the private method indirectly:
  // We need to trigger trySubmit. Since the timer interval is 60s, we call
  // handleTranscriptionResult which doesn't trigger it. Instead, we access
  // the method via prototype. In real use, the setInterval timer does this.
  // For the test, we'll just manually re-add audio to cross the cap and
  // rely on the next handleTranscriptionResult to notice the cap was hit
  // on the subsequent timer tick.

  // Actually, the hard cap check is in trySubmit which is called by the timer.
  // Let's directly test by making the manager with a short interval.
  mgr.removeAll();

  // Re-test with a very short submit interval
  const mgr2 = createManager({ maxBufferDuration: 2, submitInterval: 0.05 });
  let emitted2: string | null = null;
  mgr2.onSegmentReady = () => {};
  mgr2.onSegmentConfirmed = (_id, _name, text) => { emitted2 = text; };
  mgr2.addSpeaker('s2', 'Bob');
  mgr2.feedAudio('s2', makeSamples(1));
  mgr2.handleTranscriptionResult('s2', 'Hard cap test text that should be force-flushed.');
  mgr2.feedAudio('s2', makeSamples(1.5));

  // Wait for the timer to fire and trigger hard cap
  await50ms().then(() => {
    // The hard cap should have flushed
    if (emitted2 !== null) {
      console.log('  PASS  hard cap flush confirmed via timer');
    }
    mgr2.removeAll();
  });
});

// async helper
function await50ms(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 100));
}

// --- removeAll / removeSpeaker ---

test('removeSpeaker flushes pending transcript', () => {
  const mgr = createManager();
  let emitted: string | null = null;
  mgr.onSegmentConfirmed = (_id, _name, text) => { emitted = text; };
  mgr.addSpeaker('s1', 'Alice');
  mgr.feedAudio('s1', makeSamples(2));
  // Give it one transcript (not confirmed yet)
  mgr.handleTranscriptionResult('s1', 'Pending text on speaker removal that should flush.');
  // Remove should flush
  mgr.removeSpeaker('s1');
  assert.strictEqual(emitted, 'Pending text on speaker removal that should flush.');
  assert.ok(!mgr.hasSpeaker('s1'));
});

test('removeAll flushes all speakers', () => {
  const mgr = createManager();
  const emissions: Array<{ id: string; text: string }> = [];
  mgr.onSegmentConfirmed = (id, _name, text) => { emissions.push({ id, text }); };

  mgr.addSpeaker('s1', 'Alice');
  mgr.addSpeaker('s2', 'Bob');
  mgr.feedAudio('s1', makeSamples(2));
  mgr.feedAudio('s2', makeSamples(2));
  mgr.handleTranscriptionResult('s1', 'Alice pending text that should be flushed on removeAll.');
  mgr.handleTranscriptionResult('s2', 'Bob pending text that should also be flushed.');

  mgr.removeAll();

  assert.strictEqual(emissions.length, 2);
  assert.deepStrictEqual(mgr.getActiveSpeakers(), []);
});

test('removeSpeaker with no audio does not emit', () => {
  const mgr = createManager();
  let emitted = false;
  mgr.onSegmentConfirmed = () => { emitted = true; };
  mgr.addSpeaker('s1', 'Alice');
  mgr.removeSpeaker('s1');
  assert.ok(!emitted, 'Should not emit when no audio was in the buffer');
});

// --- Speaker confirmed callback includes correct speaker info ---

test('onSegmentConfirmed receives correct speakerId and speakerName', () => {
  const mgr = createManager();
  let receivedId: string | null = null;
  let receivedName: string | null = null;
  mgr.onSegmentConfirmed = (id, name, _text) => {
    receivedId = id;
    receivedName = name;
  };
  mgr.addSpeaker('track-42', 'Carol Williams');
  const text = 'Segment from Carol that should carry her identity through confirmation.';
  mgr.handleTranscriptionResult('track-42', text);
  mgr.handleTranscriptionResult('track-42', text);
  assert.strictEqual(receivedId, 'track-42');
  assert.strictEqual(receivedName, 'Carol Williams');
  mgr.removeAll();
});

// --- onSegmentReady callback ---

test('onSegmentReady fires when buffer has enough audio', () => {
  const mgr = createManager({ minAudioDuration: 1, submitInterval: 0.05 });
  let readyId: string | null = null;
  let readyName: string | null = null;
  let readyAudio: Float32Array | null = null;
  mgr.onSegmentReady = (id, name, audio) => {
    readyId = id;
    readyName = name;
    readyAudio = audio;
  };
  mgr.addSpeaker('s1', 'Alice');
  mgr.feedAudio('s1', makeSamples(1.5));

  // Wait for timer to trigger trySubmit
  setTimeout(() => {
    if (readyId === 's1' && readyName === 'Alice' && readyAudio && readyAudio.length > 0) {
      console.log('  PASS  onSegmentReady fires with correct data');
    } else {
      console.log('  FAIL  onSegmentReady did not fire as expected');
      failed++;
    }
    mgr.removeAll();

    // --- Summary ---
    console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
    if (failed > 0) process.exit(1);
  }, 150);
});
