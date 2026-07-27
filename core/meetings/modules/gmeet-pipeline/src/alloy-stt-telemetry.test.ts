import assert from 'node:assert/strict';

import { createAlloySttTelemetryTracker } from './alloy-stt-telemetry.js';

let failed = 0;

const check = (name: string, fn: () => void) => {
  try {
    fn();
    console.log(`  PASS ${name}`);
  } catch (error) {
    failed++;
    console.error(`  FAIL ${name}`);
    console.error(error);
  }
};

const closeTo = (actual: number | null, expected: number, epsilon = 1e-9) => {
  assert.notEqual(actual, null);
  assert.ok(Math.abs((actual as number) - expected) <= epsilon, `${actual} != ${expected}`);
};

check('ALLOY tracker moves queued audio into active and returns to idle', () => {
  const tracker = createAlloySttTelemetryTracker({
    meetingId: '101',
    nativeMeetingId: 'abc-defg-hij',
    now: () => 20_000,
  });

  tracker.captured('ch-0', 10_000);
  tracker.queued('ch-0', 4.25);
  let snapshot = tracker.snapshot();
  assert.equal(snapshot.waiting_channels, 1);
  assert.equal(snapshot.queued_audio_sec, 4.25);
  assert.equal(snapshot.active_requests, 0);

  tracker.started('ch-0', 4.25);
  snapshot = tracker.snapshot();
  assert.equal(snapshot.waiting_channels, 0);
  assert.equal(snapshot.queued_audio_sec, 0);
  assert.equal(snapshot.active_requests, 1);
  assert.equal(snapshot.active_audio_sec, 4.25);

  tracker.completed({
    channelId: 'ch-0',
    audioSec: 4.25,
    audioEndMs: 10_000,
    processingDurationMs: 2_125,
  });
  snapshot = tracker.snapshot();
  assert.equal(snapshot.active_requests, 0);
  assert.equal(snapshot.active_audio_sec, 0);
  assert.equal(snapshot.processed_windows, 1);
  assert.equal(snapshot.lag_sec, 0);
  closeTo(snapshot.rtf_ema, 0.5);
});

check('ALLOY tracker replaces one pending channel without inflating wait depth', () => {
  const tracker = createAlloySttTelemetryTracker({
    meetingId: '102',
    nativeMeetingId: 'replace-room',
  });

  tracker.queued('ch-1', 2);
  tracker.superseded('ch-1', 4.25);
  const snapshot = tracker.snapshot();

  assert.equal(snapshot.waiting_channels, 1);
  assert.equal(snapshot.queued_audio_sec, 4.25);
  assert.equal(snapshot.superseded_windows, 1);
});

check('ALLOY tracker computes lag from audio timeline rather than wall-clock silence', () => {
  let now = 20_000;
  const tracker = createAlloySttTelemetryTracker({
    meetingId: '103',
    nativeMeetingId: 'timeline-room',
    now: () => now,
  });

  tracker.captured('ch-0', 10_000);
  tracker.started('ch-0', 1);
  tracker.completed({
    channelId: 'ch-0',
    audioSec: 1,
    audioEndMs: 7_000,
    processingDurationMs: 500,
  });
  assert.equal(tracker.snapshot().lag_sec, 3);

  now += 300_000;
  assert.equal(tracker.snapshot().lag_sec, 3);
});

check('ALLOY tracker computes EMA RTF from submitted audio duration', () => {
  const tracker = createAlloySttTelemetryTracker({
    meetingId: '104',
    nativeMeetingId: 'rtf-room',
  });

  tracker.started('ch-0', 1);
  tracker.completed({
    channelId: 'ch-0',
    audioSec: 1,
    audioEndMs: 1_000,
    processingDurationMs: 800,
  });
  closeTo(tracker.snapshot().rtf_ema, 0.8);

  tracker.started('ch-0', 2);
  tracker.completed({
    channelId: 'ch-0',
    audioSec: 2,
    audioEndMs: 3_000,
    processingDurationMs: 2_000,
  });
  closeTo(tracker.snapshot().rtf_ema, 0.84);
});

check('ALLOY tracker preserves pending counters on failure and clears error on recovery', () => {
  const tracker = createAlloySttTelemetryTracker({
    meetingId: '105',
    nativeMeetingId: 'recovery-room',
  });

  tracker.started('ch-0', 3);
  tracker.queued('ch-0', 2);
  tracker.failed('ch-0', { code: 'stt_failed', message: 'worker exited' });
  let snapshot = tracker.snapshot();

  assert.equal(snapshot.active_requests, 0);
  assert.equal(snapshot.active_audio_sec, 0);
  assert.equal(snapshot.waiting_channels, 1);
  assert.equal(snapshot.queued_audio_sec, 2);
  assert.deepEqual(snapshot.last_error, { code: 'stt_failed', message: 'worker exited' });

  tracker.recovered();
  snapshot = tracker.snapshot();
  assert.equal(snapshot.last_error, null);
  assert.equal(snapshot.waiting_channels, 1);
  assert.equal(snapshot.queued_audio_sec, 2);
});

if (failed > 0) {
  console.error(`\nFAIL alloy-stt-telemetry: ${failed} check(s) failed.`);
  process.exit(1);
}

console.log('\nPASS alloy-stt-telemetry: queue, lag, RTF, and recovery contracts hold.');
