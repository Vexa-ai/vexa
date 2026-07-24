/**
 * Producer identity failures cross page→Node on their own typed observation
 * boundary. They are counted and reported, never promoted into speaker hints.
 *
 *   tsx src/producer-observation.test.ts
 */
import { makeNameUnresolvedSink } from './capture-bridge.js';

let checks = 0;
function ok(condition: boolean, message: string): void {
  if (!condition) throw new Error(`assertion failed: ${message}`);
  console.log(`  ✅ ${message}`);
  checks++;
}

const logs: string[] = [];
const observations = makeNameUnresolvedSink((message) => logs.push(message));

observations.sink({
  type: 'name-unresolved',
  platform: 'teams',
  signal: 'dom-outline',
  reason: 'resolver-empty',
  participantId: 'participant-secret-123',
  isEnd: false,
  tMs: 1000,
});

let counts = observations.counters();
ok(
  counts.total === 1 && counts.teams === 1 && counts.zoom === 0 && counts.invalid === 0,
  'one valid Teams observation is admitted and counted by platform',
);
ok(
  logs.some((message) => message.includes('name-unresolved') && message.includes('resolver-empty')),
  'the observation is reported on the Node-side telemetry channel',
);
ok(
  logs.every((message) => !message.includes('participant-secret-123')),
  'telemetry never logs the participant id',
);

observations.sink({
  type: 'name-unresolved',
  platform: 'teams',
  signal: 'dom-active',
  reason: 'resolver-empty',
  participantId: 'fixture-a',
  isEnd: false,
  tMs: 1001,
});

counts = observations.counters();
ok(
  counts.total === 1 && counts.invalid === 1,
  'a platform/signal mismatch is rejected rather than counted as evidence',
);
ok(
  logs.some((message) => message.includes('producer-observation-invalid')),
  'a malformed producer observation fails loud',
);

console.log(`\n✅ producer-observation: ${checks} checks passed — unresolved identity is visible but never becomes a hint.`);
