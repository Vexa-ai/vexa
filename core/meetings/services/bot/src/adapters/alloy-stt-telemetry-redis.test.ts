import { strict as assert } from 'node:assert';
import { createClient } from 'redis';
import {
  alloySttTelemetryKey,
  closeAlloySttTelemetryRedisClient,
  createAlloySttTelemetryPublisher,
} from './alloy-stt-telemetry-redis.js';

const meetingId = `alloy-real-redis-${process.pid}-${Date.now()}`;
const key = alloySttTelemetryKey(meetingId);
const client = createClient({ url: process.env.ALLOY_TEST_REDIS_URL ?? 'redis://127.0.0.1:6379' });
client.on('error', () => { /* the awaited operation remains the test verdict */ });

await client.connect();
try {
  let processedWindows = 1;
  const publisher = createAlloySttTelemetryPublisher({
    client,
    meetingId,
    intervalMs: 25,
    ttlSec: 5,
    readSnapshot: () => ({
      version: 1,
      meeting_id: meetingId,
      native_meeting_id: 'abc-defg-hij',
      updated_at_ms: Date.now(),
      active_requests: 1,
      active_audio_sec: 2.5,
      waiting_channels: 1,
      queued_audio_sec: 1.25,
      latest_captured_audio_end_ms: 10_000,
      latest_processed_audio_end_ms: 8_000,
      lag_sec: 2,
      rtf_ema: 0.75,
      processed_windows: processedWindows,
      superseded_windows: 3,
      last_error: null,
    }),
  });

  publisher.start();
  await publisher.publishNow();

  const firstRaw = await client.get(key);
  assert.ok(firstRaw, 'publisher must create the real Redis snapshot key');
  const first = JSON.parse(firstRaw);
  assert.equal(first.version, 1);
  assert.equal(first.meeting_id, meetingId);
  assert.equal(first.processed_windows, 1);
  assert.ok((await client.pTTL(key)) > 0, 'snapshot key must have a TTL');

  processedWindows = 2;
  await new Promise((resolve) => setTimeout(resolve, 80));
  const refreshed = JSON.parse((await client.get(key)) ?? '{}');
  assert.equal(refreshed.processed_windows, 2, 'interval must refresh the current snapshot');

  await publisher.stop();
  assert.equal(await client.exists(key), 0, 'stop must remove ended meeting telemetry');
  console.log('PASS ALLOY STT telemetry publisher uses real Redis with TTL and cleanup');
} finally {
  await client.del(key).catch(() => 0);
  await client.quit();
}

const never = new Promise<unknown>(() => undefined);
const stalledPublisher = createAlloySttTelemetryPublisher({
  client: {
    set: async () => never,
    del: async () => 1,
  },
  meetingId: 'stalled-redis',
  readSnapshot: () => ({
    version: 1,
    meeting_id: 'stalled-redis',
    native_meeting_id: 'abc-defg-hij',
    updated_at_ms: Date.now(),
    active_requests: 1,
    active_audio_sec: 1,
    waiting_channels: 0,
    queued_audio_sec: 0,
    latest_captured_audio_end_ms: 1_000,
    latest_processed_audio_end_ms: null,
    lag_sec: 1,
    rtf_ema: null,
    processed_windows: 0,
    superseded_windows: 0,
    last_error: null,
  }),
  stopTimeoutMs: 25,
});
stalledPublisher.start();
const stalledStop = await Promise.race([
  stalledPublisher.stop().then(() => 'stopped'),
  new Promise<string>((resolve) => setTimeout(() => resolve('timeout'), 150)),
]);
assert.equal(stalledStop, 'stopped', 'optional telemetry must not block bot teardown');
console.log('PASS ALLOY STT telemetry teardown is bounded when Redis stalls');

let destroyed = 0;
await closeAlloySttTelemetryRedisClient({
  quit: async () => never,
  destroy: () => {
    destroyed += 1;
  },
}, 25);
assert.equal(destroyed, 1, 'stalled telemetry Redis client must be force-closed');
console.log('PASS ALLOY STT telemetry Redis close is bounded');
